"""增量采集：扫描 Excel → 指纹比对 → 并行读取 → Parquet + 台账。

只处理“新增 / 变更”的文件；未变的文件直接跳过。台账采用 DuckDB，
业务查询直接读取 Parquet，不把大表复制进数据库。
"""
from __future__ import annotations

import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

from .config import Config, SourceConfig, load_config
from .keymap import _KEY_COLUMN_OUT, _SOURCE_COLUMN_OUT, find_key_column, key_from_filename, normalize_key

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".xls"}
_MANIFEST_SQL = """
CREATE TABLE IF NOT EXISTS files (
    path VARCHAR, source VARCHAR, size BIGINT, mtime_ns BIGINT,
    rows BIGINT, missing_key BIGINT, parquet VARCHAR, status VARCHAR, updated_at TIMESTAMP
)
"""


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^\w\-.]+", "_", path.stem)[:120]


def _read_excel(path: Path, source: SourceConfig) -> pd.DataFrame:
    df = pd.read_excel(
        path,
        sheet_name=source.sheet,
        header=max(source.header_row - 1, 0),
        engine="calamine",
    )
    df = df.dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]
    keep = [c for c in df.columns if c and not c.lower().startswith("unnamed:")]
    return df[keep]


def _read_cell(path: Path, sheet: Any, cell: str) -> str:
    """从指定单元格读产品 Key（可选功能，需要 openpyxl）。"""
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        worksheet = workbook[sheet] if isinstance(sheet, str) else workbook.worksheets[0]
        value = worksheet[cell].value
        workbook.close()
        return "" if value is None else str(value)
    except Exception:
        return ""


def _process_one(args: tuple[str, SourceConfig, str]) -> dict[str, Any]:
    path_str, source, lake_dir_str = args
    path = Path(path_str)
    try:
        df = _read_excel(path, source)
        if df.empty:
            return {
                "path": path_str,
                "source": source.id,
                "status": "empty",
                "rows": 0,
                "missing_key": 0,
                "parquet": "",
                "error": None,
            }

        key_column = find_key_column(list(df.columns), source.key.column_aliases)
        keys = (
            df[key_column].map(normalize_key)
            if key_column
            else pd.Series([""] * len(df), index=df.index)
        )
        if source.key.cell and (keys == "").all():
            keys = pd.Series(
                [normalize_key(_read_cell(path, source.sheet, source.key.cell))] * len(df),
                index=df.index,
            )
        file_key = key_from_filename(path, source.key.filename_regex)
        if file_key:
            keys = keys.mask(keys == "", file_key)

        df[_KEY_COLUMN_OUT] = keys
        df[_SOURCE_COLUMN_OUT] = path.name
        df["采集时间"] = datetime.now().isoformat(timespec="seconds")

        out_dir = Path(lake_dir_str) / "raw" / source.id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{_safe_stem(path)}.parquet"
        df.to_parquet(out_path, index=False)

        return {
            "path": path_str,
            "source": source.id,
            "status": "updated",
            "rows": int(len(df)),
            "missing_key": int((df[_KEY_COLUMN_OUT] == "").sum()),
            "parquet": str(out_path),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批
        return {
            "path": path_str,
            "source": source.id,
            "status": "failed",
            "rows": 0,
            "missing_key": 0,
            "parquet": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _unlink(path_value: Any) -> None:
    if not path_value:
        return
    try:
        Path(str(path_value)).unlink(missing_ok=True)
    except OSError:
        pass


def _upsert(con: duckdb.DuckDBPyConnection, result: dict[str, Any], source_id: str) -> None:
    old = con.execute("SELECT parquet FROM files WHERE path = ?", [result["path"]]).fetchone()
    con.execute("DELETE FROM files WHERE path = ?", [result["path"]])

    # 文件解析失败或为空时移除旧 Parquet，防止看板继续展示过期数据。
    if result["status"] != "updated" and old:
        _unlink(old[0])

    stat = Path(result["path"]).stat()
    con.execute(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            result["path"],
            source_id,
            stat.st_size,
            stat.st_mtime_ns,
            result["rows"],
            result["missing_key"],
            result.get("parquet", ""),
            result["status"],
            datetime.now(),
        ],
    )


def scan(cfg: Config) -> dict[str, list[Path]]:
    """列出每个 source 下的 Excel 文件（跳过临时文件与模板）。"""
    return cfg.files_by_source()


def refresh(cfg: Config | None = None, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """主入口：增量采集。返回刷新报告。"""
    cfg = cfg or load_config()
    cfg.settings.lake_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cfg.settings.manifest))
    con.execute(_MANIFEST_SQL)

    known = {
        row[0]: (row[1], row[2])
        for row in con.execute("SELECT path, size, mtime_ns FROM files").fetchall()
    }
    todo: list[tuple[str, SourceConfig, str]] = []
    current: set[str] = set()
    skipped = 0

    for source in cfg.sources:
        for path in cfg.source_files(source):
            current.add(str(path))
            stat = path.stat()
            if known.get(str(path)) == (stat.st_size, stat.st_mtime_ns):
                skipped += 1
            else:
                todo.append((str(path), source, str(cfg.settings.lake_dir)))

    # 源文件被删除或移出配置时，同时删除台账记录和对应 Parquet。
    deleted_records = con.execute("SELECT path, parquet FROM files").fetchall()
    deleted = [path for path, _ in deleted_records if path not in current]
    for path, parquet in deleted_records:
        if path in current:
            continue
        _unlink(parquet)
        con.execute("DELETE FROM files WHERE path = ?", [path])

    results: list[dict[str, Any]] = []
    if todo:
        workers = max(1, min(cfg.settings.workers, len(todo)))
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_one, item): item for item in todo}
            for index, future in enumerate(as_completed(futures), start=1):
                results.append(future.result())
                if progress and (index % 20 == 0 or index == len(todo)):
                    progress(f"已处理 {index}/{len(todo)} 个文件")

    for result in results:
        _upsert(con, result, result.get("source") or cfg.sources[0].id)

    from .models import rebuild_views

    rebuild_views(cfg, con)
    con.close()

    updated = [r for r in results if r["status"] == "updated"]
    return {
        "scanned": len(current),
        "updated": len(updated),
        "skipped": skipped,
        "failed": [r for r in results if r["status"] == "failed"],
        "empty": [r for r in results if r["status"] == "empty"],
        "deleted": len(deleted),
        "rows": sum(r["rows"] for r in updated),
        "missing_key": sum(r["missing_key"] for r in updated),
    }
