"""命令行入口。

用法：
    python pipeline/run.py --refresh    # 增量采集（新增/变更的 Excel）
    python pipeline/run.py --status     # 查看台账摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config  # noqa: E402
from pipeline.ingest import refresh  # noqa: E402
from pipeline.models import connect  # noqa: E402


def print_status() -> int:
    cfg = load_config()
    if not cfg.settings.manifest.exists():
        print("还没有任何数据（先运行 --refresh）")
        return 0
    con = connect(cfg, read_only=True)
    rows = con.execute(
        "SELECT source, COUNT(*) AS files, COALESCE(SUM(rows),0) AS total_rows, "
        "COALESCE(SUM(missing_key),0) AS missing_key FROM files GROUP BY source"
    ).fetchall()
    print(f"{'数据源':<16}{'文件数':>8}{'总行数':>12}{'缺Key行数':>12}")
    for source, files, total_rows, missing_key in rows:
        print(f"{source:<16}{files:>8}{total_rows:>12}{missing_key:>12}")
    con.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Excel 增量采集")
    parser.add_argument("--refresh", action="store_true", help="扫描并采集新增/变更的 Excel")
    parser.add_argument("--status", action="store_true", help="查看台账摘要")
    args = parser.parse_args()

    cfg = load_config()
    if args.refresh or not args.status:
        report = refresh(cfg, progress=lambda msg: print(msg, flush=True))
        print(json.dumps({k: v for k, v in report.items() if k != "failed"}, ensure_ascii=False, indent=2, default=str))
        if report["failed"]:
            print("\n失败文件：", file=sys.stderr)
            for item in report["failed"]:
                print(f"  {item['path']} -> {item['error']}", file=sys.stderr)
        if args.status:
            print()
            print_status()
        return 0
    return print_status()


if __name__ == "__main__":
    raise SystemExit(main())
