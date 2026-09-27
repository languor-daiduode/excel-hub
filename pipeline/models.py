"""DuckDB 模型层：把 Parquet 湖挂成视图，供查询与看板使用。"""
from __future__ import annotations

import duckdb

from .config import Config, load_config


def connect(cfg: Config | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    cfg = cfg or load_config()
    cfg.settings.manifest.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(cfg.settings.manifest), read_only=read_only)


def source_parquet_files(cfg: Config, source_id: str) -> list[str]:
    raw_dir = cfg.settings.lake_dir / "raw" / source_id
    return [str(path) for path in sorted(raw_dir.glob("*.parquet"))]


def rebuild_views(cfg: Config | None = None, con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """为每个 source 创建/刷新视图（视图直接读 Parquet，不复制数据）。"""
    cfg = cfg or load_config()
    own = con is None
    connection = con or connect(cfg)
    created: list[str] = []

    for source in cfg.sources:
        if not source_parquet_files(cfg, source.id):
            connection.execute(f'DROP VIEW IF EXISTS "{source.id}"')
            continue

        glob = (cfg.settings.lake_dir / "raw" / source.id / "*.parquet").as_posix()
        safe_glob = glob.replace("'", "''")
        connection.execute(
            f'CREATE OR REPLACE VIEW "{source.id}" AS '
            f"SELECT * FROM read_parquet('{safe_glob}', union_by_name = true, filename = true)"
        )
        created.append(source.id)

    if own:
        connection.close()
    return created


def describe(con: duckdb.DuckDBPyConnection, view: str) -> list[tuple[str, str]]:
    """返回视图的 (列名, 类型) 列表。"""
    safe_view = view.replace('"', '""')
    return [(row[0], row[1]) for row in con.execute(f'DESCRIBE SELECT * FROM "{safe_view}"').fetchall()]


def view_names(cfg: Config | None = None, con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """返回当前存在且可查询的数据源视图。"""
    cfg = cfg or load_config()
    own = con is None
    connection = con or connect(cfg, read_only=True)
    names: list[str] = []
    try:
        for source in cfg.sources:
            if not source_parquet_files(cfg, source.id):
                continue
            try:
                safe_view = source.id.replace('"', '""')
                connection.execute(f'SELECT 1 FROM "{safe_view}" LIMIT 0')
                names.append(source.id)
            except Exception:
                continue
    finally:
        if own:
            connection.close()
    return names
