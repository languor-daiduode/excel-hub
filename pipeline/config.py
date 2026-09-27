"""配置加载：把 config/sources.yaml 解析成类型化对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.yaml"
DATA_ROOT = PROJECT_ROOT  # 独立仓库根目录


@dataclass
class KeyConfig:
    column_aliases: list[str] = field(default_factory=list)
    cell: str | None = None
    filename_regex: str = "^(?P<key>[^_]+)"


@dataclass
class SourceConfig:
    id: str
    glob: str
    sheet: Any = 0
    header_row: int = 1
    skip_contains: list[str] = field(default_factory=list)
    key: KeyConfig = field(default_factory=KeyConfig)
    date_column_aliases: list[str] = field(default_factory=list)
    time_column_aliases: list[str] = field(default_factory=list)


@dataclass
class Settings:
    lake_dir: Path
    manifest: Path
    workers: int = 8
    top_n: int = 20
    max_filter_values: int = 50


@dataclass
class Config:
    project_root: Path
    data_root: Path
    sources: list[SourceConfig]
    settings: Settings

    def source_files(self, source: SourceConfig) -> list[Path]:
        """按 glob 找到该 source 的所有 Excel 文件（并应用 skip_contains）。"""
        files = sorted(self.data_root.glob(source.glob))
        result = []
        for path in files:
            if not path.is_file():
                continue
            name = path.name
            if any(token and token in name for token in source.skip_contains):
                continue
            result.append(path)
        return result

    def files_by_source(self) -> dict[str, list[Path]]:
        return {s.id: self.source_files(s) for s in self.sources}


def load_config(path: Path | None = None) -> Config:
    config_path = Path(path) if path else CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    sources: list[SourceConfig] = []
    for item in raw.get("sources") or []:
        key_raw = item.get("key") or {}
        sources.append(
            SourceConfig(
                id=str(item["id"]),
                glob=str(item.get("glob", "")),
                sheet=item.get("sheet", 0),
                header_row=int(item.get("header_row", 1)),
                skip_contains=list(item.get("skip_contains") or []),
                key=KeyConfig(
                    column_aliases=list(key_raw.get("column_aliases") or []),
                    cell=key_raw.get("cell"),
                    filename_regex=str(key_raw.get("filename_regex") or "^(?P<key>[^_]+)"),
                ),
                date_column_aliases=list(item.get("date_column_aliases") or []),
                time_column_aliases=list(item.get("time_column_aliases") or []),
            )
        )

    settings_raw = raw.get("settings") or {}
    lake_dir = PROJECT_ROOT / str(settings_raw.get("lake_dir", "lake"))
    manifest = PROJECT_ROOT / str(settings_raw.get("manifest", "lake/manifest.duckdb"))
    settings = Settings(
        lake_dir=lake_dir,
        manifest=manifest,
        workers=int(settings_raw.get("workers", 8)),
        top_n=int(settings_raw.get("top_n", 20)),
        max_filter_values=int(settings_raw.get("max_filter_values", 50)),
    )
    return Config(project_root=PROJECT_ROOT, data_root=DATA_ROOT, sources=sources, settings=settings)



