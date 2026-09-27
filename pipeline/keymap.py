"""Key 统一：标准化 + 三级推导（行内列 → 单元格 → 文件名）。"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

# 常见交易代码后缀（标准化时去掉，需保留后缀时可改这里）
_SUFFIXES = (".SH", ".SZ", ".BJ", ".HK", ".OF")
_KEY_COLUMN_OUT = "产品Key"
_SOURCE_COLUMN_OUT = "来源文件"


def normalize_key(value: object) -> str:
    """把任意写法的 Key 归一化：去空格、全角转半角、大写、去市场后缀。"""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)  # ５１２８８０ -> 512880
    text = re.sub(r"\s+", "", text)
    text = text.upper()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if re.fullmatch(r"\d+\.0", text):  # Excel 把 512880 读成 512880.0
        text = text[:-2]
    return text


def key_from_filename(path: Path, pattern: str) -> str:
    """从文件名提取 Key，例如 512880_证券ETF.xlsx -> 512880。"""
    match = re.search(pattern, path.stem)
    if not match:
        return ""
    group = match.groupdict().get("key")
    raw = group if group is not None else match.group(0)
    return normalize_key(raw)


def find_key_column(columns: list[str], aliases: list[str]) -> str | None:
    """在表头里找 Key 列（支持别名、忽略大小写与空格）。"""
    normalized = {str(c).strip().upper(): str(c) for c in columns}
    for alias in aliases:
        hit = normalized.get(str(alias).strip().upper())
        if hit:
            return hit
    return None
