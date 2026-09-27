"""Excel 汇总情况简报 —— Streamlit 看板。

启动：
    ..\\venv\\Scripts\\python.exe -m streamlit run app\\dashboard.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import load_config  # noqa: E402
from pipeline.ingest import refresh  # noqa: E402
from pipeline.models import connect, view_names  # noqa: E402

st.set_page_config(page_title="Excel Hub", page_icon="📊", layout="wide")

METADATA_COLUMNS = {"产品Key", "来源文件", "采集时间", "filename"}
DATE_HINTS = ("日期", "时间", "date", "time")
NUMERIC_HINTS = ("价格", "数量", "金额", "手续费", "成本", "收益", "盈亏", "成交量", "成交额", "price", "qty", "amount")
NUMERIC_TYPES = ("INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL", "HUGEINT", "UBIGINT", "BIGINT")
DATE_TYPES = ("DATE", "TIMESTAMP", "TIME")


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def as_text(expr: str) -> str:
    return f"COALESCE(CAST({expr} AS VARCHAR), '(空)' )"


def describe_view(con, view: str) -> list[tuple[str, str]]:
    safe_view = view.replace('"', '""')
    rows = con.execute(f'DESCRIBE SELECT * FROM "{safe_view}"').fetchall()
    return [(str(row[0]), str(row[1]).upper()) for row in rows]


def fetch_df(con, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).fetchdf()


def choose_default(options: list[str], preferred: list[str]) -> str | None:
    for item in preferred:
        if item in options:
            return item
    return options[0] if options else None


def aggregate_sql(metric: str, aggregation: str) -> str:
    if metric == "记录数":
        return "COUNT(*)"
    value_expr = f"TRY_CAST({quote_ident(metric)} AS DOUBLE)"
    return {
        "求和": f"COALESCE(SUM({value_expr}), 0)",
        "平均": f"AVG({value_expr})",
        "最大值": f"MAX({value_expr})",
        "最小值": f"MIN({value_expr})",
        "计数": f"COUNT({value_expr})",
    }[aggregation]


def show_refresh_report() -> None:
    report = st.session_state.pop("refresh_report", None)
    if not report:
        return
    st.success(
        f"刷新完成：扫描 {report['scanned']} 个文件，更新 {report['updated']} 个，"
        f"跳过 {report['skipped']} 个，新增/更新 {report['rows']:,} 行。"
    )
    cols = st.columns(4)
    cols[0].metric("新采集行", f"{report['rows']:,}")
    cols[1].metric("缺 Key 行", f"{report['missing_key']:,}")
    cols[2].metric("空文件", len(report.get("empty", [])))
    cols[3].metric("失败文件", len(report.get("failed", [])))
    if report.get("failed"):
        with st.expander("查看失败文件", expanded=True):
            st.dataframe(pd.DataFrame(report["failed"]), width="stretch", hide_index=True)


def build_filters(
    con,
    view: str,
    source_cfg,
    columns: list[str],
    date_col: str | None,
    dimensions: list[str],
) -> tuple[str, list[Any], dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    state: dict[str, Any] = {}

    key_col = "产品Key" if "产品Key" in columns else None
    if key_col:
        key_count = con.execute(
            f"SELECT COUNT(DISTINCT {quote_ident(key_col)}) FROM {quote_ident(view)}"
        ).fetchone()[0]
        if key_count <= source_cfg.max_filter_values:
            keys = [
                row[0]
                for row in con.execute(
                    f"SELECT DISTINCT {quote_ident(key_col)} FROM {quote_ident(view)} "
                    f"WHERE COALESCE(CAST({quote_ident(key_col)} AS VARCHAR), '') <> '' "
                    f"ORDER BY 1 LIMIT {source_cfg.max_filter_values}"
                ).fetchall()
            ]
            selected_keys = st.multiselect("产品 Key（可搜索）", keys)
            if selected_keys:
                marks = ", ".join(["?"] * len(selected_keys))
                conditions.append(f"CAST({quote_ident(key_col)} AS VARCHAR) IN ({marks})")
                params.extend(selected_keys)
        else:
            key_search = st.text_input(f"产品 Key 包含（共 {key_count:,} 个）", "")
            if key_search.strip():
                conditions.append(f"CAST({quote_ident(key_col)} AS VARCHAR) ILIKE ?")
                params.append(f"%{key_search.strip()}%")
        state["key_count"] = key_count

    file_col = "来源文件" if "来源文件" in columns else None
    if file_col:
        file_count = con.execute(
            f"SELECT COUNT(DISTINCT {quote_ident(file_col)}) FROM {quote_ident(view)}"
        ).fetchone()[0]
        if file_count <= source_cfg.max_filter_values:
            files = [
                row[0]
                for row in con.execute(
                    f"SELECT DISTINCT {quote_ident(file_col)} FROM {quote_ident(view)} ORDER BY 1 "
                    f"LIMIT {source_cfg.max_filter_values}"
                ).fetchall()
            ]
            selected_files = st.multiselect("来源文件", files)
            if selected_files:
                marks = ", ".join(["?"] * len(selected_files))
                conditions.append(f"CAST({quote_ident(file_col)} AS VARCHAR) IN ({marks})")
                params.extend(selected_files)
        else:
            file_search = st.text_input(f"文件名包含（共 {file_count:,} 个）", "")
            if file_search.strip():
                conditions.append(f"CAST({quote_ident(file_col)} AS VARCHAR) ILIKE ?")
                params.append(f"%{file_search.strip()}%")

    if date_col:
        date_expr = f"TRY_CAST({quote_ident(date_col)} AS TIMESTAMP)"
        bounds = con.execute(
            f"SELECT MIN({date_expr}), MAX({date_expr}) FROM {quote_ident(view)} "
            f"WHERE {date_expr} IS NOT NULL"
        ).fetchone()
        if bounds and bounds[0] is not None and bounds[1] is not None:
            min_date = pd.Timestamp(bounds[0]).date()
            max_date = pd.Timestamp(bounds[1]).date()
            selected_range = st.date_input(
                f"日期范围（{date_col}）",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
            )
            if isinstance(selected_range, (tuple, list)) and len(selected_range) == 2:
                start_date, end_date = selected_range
                conditions.append(f"{date_expr} >= ?")
                params.append(pd.Timestamp(start_date))
                conditions.append(f"{date_expr} < ?")
                params.append(pd.Timestamp(end_date) + pd.Timedelta(days=1))
                state["date_range"] = (start_date, end_date)

    with st.expander("维度筛选", expanded=False):
        filter_dims = [c for c in dimensions if c not in {"产品Key", "来源文件"}]
        extra_col = st.selectbox("筛选字段", ["不筛选"] + filter_dims, key="extra_filter_col")
        if extra_col != "不筛选":
            value_count = con.execute(
                f"SELECT COUNT(DISTINCT {quote_ident(extra_col)}) FROM {quote_ident(view)}"
            ).fetchone()[0]
            if value_count <= source_cfg.max_filter_values:
                values = [
                    row[0]
                    for row in con.execute(
                        f"SELECT DISTINCT {quote_ident(extra_col)} FROM {quote_ident(view)} "
                        f"WHERE {quote_ident(extra_col)} IS NOT NULL ORDER BY 1 "
                        f"LIMIT {source_cfg.max_filter_values}"
                    ).fetchall()
                ]
                selected_values = st.multiselect(
                    f"{extra_col} 取值", [str(v) for v in values], key="extra_filter_values"
                )
                if selected_values:
                    marks = ", ".join(["?"] * len(selected_values))
                    conditions.append(f"CAST({quote_ident(extra_col)} AS VARCHAR) IN ({marks})")
                    params.extend(selected_values)
            else:
                search_value = st.text_input(f"{extra_col} 包含", "", key="extra_filter_search")
                if search_value.strip():
                    conditions.append(f"CAST({quote_ident(extra_col)} AS VARCHAR) ILIKE ?")
                    params.append(f"%{search_value.strip()}%")

    where_sql = " AND ".join(conditions) if conditions else "TRUE"
    return where_sql, params, state


def grouped_data(
    con,
    view: str,
    group_col: str,
    agg_expr: str,
    where_sql: str,
    params: list[Any],
    top_n: int,
) -> pd.DataFrame:
    group_expr = as_text(quote_ident(group_col))
    sql = f"""
        SELECT {group_expr} AS 维度,
               {agg_expr} AS 指标值,
               COUNT(*) AS 记录数
        FROM {quote_ident(view)}
        WHERE {where_sql}
        GROUP BY 1
        ORDER BY 指标值 DESC NULLS LAST
        LIMIT {int(top_n)}
    """
    return fetch_df(con, sql, params)


def trend_data(
    con,
    view: str,
    date_col: str,
    group_col: str | None,
    granularity: str,
    agg_expr: str,
    where_sql: str,
    params: list[Any],
) -> pd.DataFrame:
    date_expr = f"TRY_CAST({quote_ident(date_col)} AS TIMESTAMP)"
    grain = {"日": "day", "周": "week", "月": "month"}[granularity]
    if group_col:
        group_expr = as_text(quote_ident(group_col))
        sql = f"""
            SELECT date_trunc('{grain}', {date_expr}) AS 时间,
                   {group_expr} AS 分组,
                   {agg_expr} AS 指标值,
                   COUNT(*) AS 记录数
            FROM {quote_ident(view)}
            WHERE {where_sql} AND {date_expr} IS NOT NULL
            GROUP BY 1, 2
            ORDER BY 1, 2
        """
    else:
        sql = f"""
            SELECT date_trunc('{grain}', {date_expr}) AS 时间,
                   {agg_expr} AS 指标值,
                   COUNT(*) AS 记录数
            FROM {quote_ident(view)}
            WHERE {where_sql} AND {date_expr} IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """
    return fetch_df(con, sql, params)


def render_dashboard(con, cfg) -> None:
    available = view_names(cfg, con)
    if not available:
        st.info("还没有可展示的数据。请把 Excel 放入 `汇总情况简报excel` 后，点击左侧“刷新数据”。")
        return

    st.sidebar.subheader("数据源")
    source = st.sidebar.selectbox("选择数据源", available)
    source_cfg = next(item for item in cfg.sources if item.id == source)
    columns_with_types = describe_view(con, source)
    columns = [name for name, _ in columns_with_types]
    type_by_column = dict(columns_with_types)
    visible_columns = [c for c in columns if c not in METADATA_COLUMNS]

    # 数据质量与文件状态
    manifest = fetch_df(
        con,
        """
        SELECT source, COUNT(*) AS 文件数, COALESCE(SUM(rows), 0) AS 总行数,
               COALESCE(SUM(missing_key), 0) AS 缺Key行数, MAX(updated_at) AS 最后更新
        FROM files
        WHERE source = ?
        GROUP BY source
        """,
        [source],
    )
    latest = manifest.loc[0, "最后更新"] if not manifest.empty else "—"
    top_cols = st.columns(4)
    top_cols[0].metric("数据源", source)
    top_cols[1].metric("Excel 文件", f"{int(manifest.loc[0, '文件数']):,}" if not manifest.empty else "0")
    top_cols[2].metric("数据行", f"{int(manifest.loc[0, '总行数']):,}" if not manifest.empty else "0")
    top_cols[3].metric("缺 Key 行", f"{int(manifest.loc[0, '缺Key行数']):,}" if not manifest.empty else "0")
    st.caption(f"最后刷新：{latest}")

    # 字段自动识别
    date_options = [c for c in visible_columns if any(h in c.lower() for h in DATE_HINTS) or any(t in type_by_column[c] for t in DATE_TYPES)]
    default_date = choose_default(date_options, source_cfg.date_column_aliases)
    numeric_options = [
        c
        for c in visible_columns
        if c not in date_options and (
            any(t in type_by_column[c] for t in NUMERIC_TYPES)
            or type_by_column[c] in {"INTEGER", "NUMERIC"}
            or any(h in c.lower() for h in NUMERIC_HINTS)
        )
    ]
    dimension_options = [c for c in visible_columns if c not in numeric_options]
    if "产品Key" in columns:
        dimension_options = ["产品Key"] + [c for c in dimension_options if c != "产品Key"]
    if not dimension_options and columns:
        dimension_options = [columns[0]]

    st.sidebar.subheader("时间与指标")
    date_col = st.sidebar.selectbox(
        "日期字段", ["不使用日期"] + date_options,
        index=(date_options.index(default_date) + 1 if default_date else 0),
    )
    date_col = None if date_col == "不使用日期" else date_col
    metric = st.sidebar.selectbox("指标", ["记录数"] + numeric_options)
    aggregation = st.sidebar.selectbox(
        "聚合方式", ["求和", "平均", "最大值", "最小值", "计数"], disabled=metric == "记录数"
    )
    if metric == "记录数":
        aggregation = "求和"
    agg_expr = aggregate_sql(metric, aggregation)

    top_n = st.sidebar.slider("图表最多显示", 5, 100, int(cfg.settings.top_n), 5)

    with st.sidebar.expander("筛选条件", expanded=True):
        where_sql, params, filter_state = build_filters(
            con, source, cfg.settings, columns, date_col, dimension_options
        )

    if metric != "记录数" and aggregation == "求和" and any(h in metric.lower() for h in ("价格", "price", "费率")):
        st.info(f"提示：{metric} 当前使用“求和”。做价格比较时通常应切换为“平均”。")

    filtered_rows = con.execute(
        f"SELECT COUNT(*) FROM {quote_ident(source)} WHERE {where_sql}", params
    ).fetchone()[0]
    st.caption(f"当前筛选结果：{filtered_rows:,} 行")

    tab_trend, tab_compare, tab_rank, tab_portion, tab_detail = st.tabs(
        ["趋势", "对比", "排行", "占比", "明细"]
    )

    with tab_trend:
        if not date_col:
            st.info("未选择日期字段，暂时无法生成趋势图。请在左侧选择“日期字段”。")
        else:
            c1, c2 = st.columns(2)
            with c1:
                granularity = st.radio("时间粒度", ["日", "周", "月"], horizontal=True)
            with c2:
                trend_group_options = ["不分组"] + dimension_options
                default_group = "产品Key" if "产品Key" in dimension_options else "不分组"
                trend_group = st.selectbox(
                    "趋势分组", trend_group_options,
                    index=trend_group_options.index(default_group),
                )
            df = trend_data(
                con, source, date_col, None if trend_group == "不分组" else trend_group,
                granularity, agg_expr, where_sql, params,
            )
            if df.empty:
                st.info("当前筛选条件下没有可绘制的日期数据。")
            else:
                df["时间"] = pd.to_datetime(df["时间"])
                if trend_group != "不分组":
                    top_groups = (
                        df.groupby("分组", dropna=False)["指标值"].sum().abs()
                        .nlargest(top_n).index
                    )
                    df = df[df["分组"].isin(top_groups)]
                    fig = px.line(df, x="时间", y="指标值", color="分组", markers=True)
                else:
                    fig = px.line(df, x="时间", y="指标值", markers=True)
                fig.update_layout(height=520, hovermode="x unified", xaxis_title=date_col, yaxis_title=metric)
                st.plotly_chart(fig, width="stretch")
                with st.expander("查看趋势数据"):
                    st.dataframe(df, width="stretch", hide_index=True)

    with tab_compare:
        default_dim = "产品Key" if "产品Key" in dimension_options else choose_default(dimension_options, [])
        compare_dim = st.selectbox("对比维度", dimension_options, index=dimension_options.index(default_dim) if default_dim else 0)
        df = grouped_data(con, source, compare_dim, agg_expr, where_sql, params, top_n)
        if df.empty:
            st.info("当前筛选条件下没有可对比的数据。")
        else:
            fig = px.bar(df, x="维度", y="指标值", text_auto=".3s", hover_data=["记录数"])
            fig.update_layout(height=520, xaxis_title=compare_dim, yaxis_title=metric)
            st.plotly_chart(fig, width="stretch")
            with st.expander("查看对比数据"):
                st.dataframe(df, width="stretch", hide_index=True)

    with tab_rank:
        default_dim = "产品Key" if "产品Key" in dimension_options else choose_default(dimension_options, [])
        rank_dim = st.selectbox("排行维度", dimension_options, index=dimension_options.index(default_dim) if default_dim else 0, key="rank_dim")
        df = grouped_data(con, source, rank_dim, agg_expr, where_sql, params, top_n)
        if df.empty:
            st.info("当前筛选条件下没有可排行的数据。")
        else:
            plot_df = df.sort_values("指标值", ascending=True)
            fig = px.bar(plot_df, x="指标值", y="维度", orientation="h", text_auto=".3s", hover_data=["记录数"])
            fig.update_layout(height=max(420, 28 * len(plot_df)), yaxis_title=rank_dim, xaxis_title=metric)
            st.plotly_chart(fig, width="stretch")

    with tab_portion:
        default_dim = "产品Key" if "产品Key" in dimension_options else choose_default(dimension_options, [])
        portion_dim = st.selectbox("占比维度", dimension_options, index=dimension_options.index(default_dim) if default_dim else 0, key="portion_dim")
        sql = f"""
            SELECT {as_text(quote_ident(portion_dim))} AS 维度, COUNT(*) AS 记录数
            FROM {quote_ident(source)}
            WHERE {where_sql}
            GROUP BY 1
            ORDER BY 记录数 DESC
        """
        df = fetch_df(con, sql, params)
        if df.empty:
            st.info("当前筛选条件下没有可统计的数据。")
        else:
            if len(df) > top_n:
                head = df.head(max(top_n - 1, 1)).copy()
                other = pd.DataFrame([{"维度": "其他", "记录数": int(df.iloc[max(top_n - 1):]["记录数"].sum())}])
                df = pd.concat([head, other], ignore_index=True)
            fig = px.pie(df, names="维度", values="记录数", hole=0.45)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(height=540)
            st.plotly_chart(fig, width="stretch")
            st.caption("占比按当前筛选条件下的记录数计算。")

    with tab_detail:
        limit = st.selectbox("显示行数", [100, 500, 1000, 5000, 20000], index=2)
        detail = fetch_df(
            con,
            f"SELECT * FROM {quote_ident(source)} WHERE {where_sql} LIMIT {int(limit)}",
            params,
        )
        st.dataframe(detail, width="stretch", hide_index=True, height=520)
        st.download_button(
            "下载当前明细 CSV",
            data=detail.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{source}_明细.csv",
            mime="text/csv",
        )


def main() -> None:
    cfg = load_config()
    st.title("📊 Excel Hub")
    st.caption("多 Excel 增量汇总与可视化看板 | Incremental Excel aggregation and dashboard")

    with st.sidebar:
        st.header("数据刷新")
        if st.button("刷新数据", type="primary", width="stretch"):
            with st.spinner("正在扫描并读取新增/变更的 Excel…"):
                report = refresh(cfg, progress=lambda message: None)
            st.session_state["refresh_report"] = report
            st.rerun()
        st.caption("只会重读新增或修改过的文件，未变文件会跳过。")
    show_refresh_report()

    if not cfg.settings.manifest.exists():
        st.info("还没有台账。请先点击左侧“刷新数据”。")
        return

    con = connect(cfg)
    try:
        render_dashboard(con, cfg)
    finally:
        con.close()


if __name__ == "__main__":
    main()




