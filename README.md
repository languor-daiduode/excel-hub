# Excel Hub

> **中文简介：** Excel Hub 是一个本机运行的 Excel 增量汇总与可视化工具。它把持续追加或新增的 Excel 统一关联到产品 Key，并使用 DuckDB、Parquet 和 Streamlit 生成趋势、对比、排行、占比与明细看板。
>
> **English:** Excel Hub is a local-first toolkit for incrementally aggregating Excel files into an interactive dashboard. It maps records to a unified product key, caches changed sheets as Parquet, and uses DuckDB and Streamlit to visualize trends, comparisons, rankings, proportions, and details.

## 功能 / Features

- 中文：文件未变化时自动跳过，只重读新增或修改过的 Excel。
- 中文：支持行内 Key、指定单元格和文件名三种 Key 推导方式。
- 中文：并行处理多个 Excel，并把结果缓存为 Parquet。
- 中文：看板包含趋势、对比、排行、占比和明细，可手动刷新。
- English: Skips unchanged files and reprocesses only new or modified spreadsheets.
- English: Resolves product keys from a column, a cell, or the filename.
- English: Processes workbooks in parallel and caches results as Parquet.
- English: Provides trend, comparison, ranking, proportion, and detail views with manual refresh.

## 数据流 / Data Flow

```text
Excel files
    ↓  pandas + python-calamine
Normalized product key
    ↓  PyArrow
Parquet cache
    ↓  DuckDB views
Streamlit + Plotly dashboard
```

## 快速开始 / Quick Start

### Windows

```powershell
cd C:\path\to\excel-hub
py -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe pipeline\run.py --refresh
venv\Scripts\python.exe -m streamlit run app\dashboard.py
```

也可以双击 `run.bat`。命令窗口保持打开，然后访问：

```text
http://127.0.0.1:8501
```

### macOS / Linux

```bash
cd /path/to/excel-hub
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python pipeline/run.py --refresh
streamlit run app/dashboard.py
```

## Excel 数据 / Excel Data

默认把 `.xlsx` 文件放在 `data/` 目录。仓库内包含两份脱敏示例：

```text
data/512880_证券ETF.xlsx
data/159915_创业板ETF.xlsx
```

推荐表头：

```text
交易日期 | 交易时间 | 产品Key | 产品名称 | 买卖方向 | 价格 | 数量 | 金额 | 手续费 | 备注
```

填写模板位于 `templates/_模板_交易历史.xlsx`。

### Key 规则 / Key Resolution

程序按以下顺序寻找非空 Key：

1. 行内 Key 列，例如 `产品Key`、`产品代码`、`代码`、`Key`。
2. 配置指定的单元格，例如 `B1`。
3. 文件名中第一个下划线之前的内容。

```text
512880_证券ETF.xlsx    -> 512880
159915_创业板ETF.xlsx  -> 159915
```

如果一个新的 Excel 只代表一个产品，也可以不填写 Key 列，直接使用“代码_名称.xlsx”的文件名。

## 配置 / Configuration

数据源配置位于 `config/sources.yaml`：

```yaml
sources:
  - id: 交易历史
    glob: "data/*.xlsx"
    sheet: 0
    header_row: 1
    skip_contains: ["~$", "_模板"]
    key:
      column_aliases: ["产品Key", "产品代码", "代码", "Key"]
      cell: null
      filename_regex: "^(?P<key>[^_]+)"
```

- `sheet`：`0` 表示第一个工作表，也可以写工作表名称。
- `header_row`：表头所在行，第一行写 `1`。
- `glob`：Excel 搜索路径。
- `workers`：并行读取进程数，默认 8。
- `top_n`：排行榜默认显示数量。

## 增量机制 / Incremental Refresh

Excel Hub 在 `lake/manifest.duckdb` 中记录文件大小、修改时间、行数、缺失 Key 数和 Parquet 路径。刷新时：

- 新文件：读取并写入 Parquet。
- 已修改文件：重新读取并覆盖对应 Parquet。
- 未变化文件：跳过。
- 已删除文件：删除台账记录和对应 Parquet。

## 项目结构 / Project Layout

```text
excel-hub/
├── app/dashboard.py          # Streamlit dashboard
├── config/sources.yaml       # Source and key configuration
├── data/                     # Excel input directory
├── pipeline/                 # Scan, ingest, key mapping, DuckDB views
├── templates/                # Excel template
├── lake/                     # Generated cache; ignored by Git
├── requirements.txt
├── run.bat                   # Windows one-click launcher
└── README.md
```

## 隐私 / Privacy

- 所有处理都在本机完成，不调用云端 AI 或第三方数据服务。
- `lake/`、Parquet、DuckDB 和真实业务 Excel 默认被 `.gitignore` 排除。
- 开源仓库只保留两份明确标记的脱敏示例数据。

- All processing runs locally; no cloud AI or third-party data service is required.
- Generated caches and real business spreadsheets are excluded by `.gitignore`.
- Only the two clearly labeled sanitized examples are intended for the public repository.

## 已知边界 / Limitations

- 当前版本面向单机、手动刷新和中大规模数据。
- 看板查询直接读取 Parquet；数据达到数亿行后，建议增加预聚合表或分区策略。
- 当前不包含登录、权限隔离和多用户并发写入。

## License

MIT
