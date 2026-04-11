from pathlib import Path
import logging
import re
from typing import List

from sqlglot import expressions as exp, parse_one

from io_excel import read_excel_data, write_output_excel
from pipeline import build_output_dataframe


def _split_and_predicates(expression: exp.Expression) -> List[exp.Expression]:
    if isinstance(expression, exp.And):
        return _split_and_predicates(expression.this) + _split_and_predicates(expression.expression)
    return [expression]


def _extract_predicates_from_text(where_text: str) -> List[str]:
    """Tách predicate theo AND ở top-level bằng SQL parser (an toàn cho BETWEEN ... AND ...)."""
    cleaned = re.sub(r"(?i)^WHERE\s+", "", (where_text or "").strip()).strip()
    cleaned = re.sub(r"(?i)^AND\s+", "", cleaned).strip()
    if not cleaned:
        return []

    try:
        stmt = parse_one(f"SELECT 1 FROM DUAL WHERE {cleaned}", read="oracle")
        where = stmt.args.get("where")
        if not isinstance(where, exp.Where) or where.this is None:
            return [cleaned]
        predicates = _split_and_predicates(where.this)
        result = [item.sql(dialect="oracle").strip() for item in predicates if isinstance(item, exp.Expression)]
        return [item for item in result if item]
    except Exception:
        # Fallback: giữ nguyên nguyên cụm để tránh split sai cú pháp.
        return [cleaned]


def _merge_unique_where(values) -> str:
    """Gộp WHERE_CONDITION theo từng predicate, loại trùng theo bảng."""

    def _normalize_key(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(?i)^AND\s+", "", text).strip()
        return text.upper()

    seen: set[str] = set()
    predicates: list[str] = []

    for value in values:
        raw = str(value or "")
        if not raw.strip():
            continue

        # Tách theo ';' trước (thường ngăn cách từng nhóm điều kiện),
        # sau đó tách AND top-level bằng parser.
        chunks = [chunk.strip() for chunk in re.split(r"[;]+", raw) if chunk.strip()]
        for chunk in chunks:
            for predicate in _extract_predicates_from_text(chunk):
                key = _normalize_key(predicate)
                if not key or key in seen:
                    continue
                seen.add(key)
                predicates.append(predicate)

    if not predicates:
        return ""
    if len(predicates) == 1:
        return predicates[0]
    return predicates[0] + "\nAND " + "\nAND ".join(predicates[1:])
    
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    data_path = Path(__file__).resolve().parent / "data" / "ETL_SCRIPT_KM_ETL.xlsx"
    output_path = Path(__file__).resolve().parent / "output" / "ETL_SCRIPT_KM_ETL_SCHEMA_TABLE_COLUMN.xlsx"

    df = read_excel_data(data_path)
    output_df = build_output_dataframe(df)
    data_mask = (
        output_df["SCHEMA"].fillna("").ne("")
        | output_df["TABLE"].fillna("").ne("")
        | output_df["DBLINK"].fillna("").ne("")
        | output_df["COLUMN"].fillna("").ne("")
    )
    output_df = output_df.loc[data_mask]

    # Tách riêng xử lý WHERE_CONDITION theo cấp bảng để không bị mất do dedup theo cột.
    table_where_df = output_df.loc[
        output_df["WHERE_CONDITION"].fillna("").astype(str).str.strip().ne(""),
        ["SCHEMA", "TABLE", "DBLINK", "WHERE_CONDITION"],
    ].copy()
    table_where_df["WHERE_CONDITION"] = table_where_df["WHERE_CONDITION"].astype(str).str.strip()
    table_where_df = table_where_df.drop_duplicates(
        subset=["SCHEMA", "TABLE", "DBLINK", "WHERE_CONDITION"]
    )
    table_where_map = (
        table_where_df
        .groupby(["SCHEMA", "TABLE", "DBLINK"], dropna=False, sort=False)["WHERE_CONDITION"]
        .agg(_merge_unique_where)
        .reset_index()
        .rename(columns={"WHERE_CONDITION": "_WHERE_CONDITION_TABLE"})
    )

    output_df["_has_where"] = output_df["WHERE_CONDITION"].fillna("").str.len().gt(0).astype(int)
    output_df["_where_len"] = output_df["WHERE_CONDITION"].fillna("").str.len()
    output_df = output_df.sort_values(
        by=["SCHEMA", "TABLE", "DBLINK", "COLUMN", "_has_where", "_where_len"],
        ascending=[True, True, True, True, False, False],
        kind="mergesort",
        na_position="last",
    )
    output_df = output_df.drop_duplicates(subset=["SCHEMA", "TABLE", "DBLINK", "COLUMN"]).reset_index(drop=True)

    if not table_where_map.empty:
        output_df = output_df.merge(
            table_where_map,
            on=["SCHEMA", "TABLE", "DBLINK"],
            how="left",
        )
        output_df["WHERE_CONDITION"] = output_df["_WHERE_CONDITION_TABLE"].where(
            output_df["_WHERE_CONDITION_TABLE"].fillna("").str.strip().ne(""),
            output_df["WHERE_CONDITION"],
        )

    output_df = output_df.drop(columns=["_has_where", "_where_len"], errors="ignore")
    output_df = output_df.drop(columns=["_WHERE_CONDITION_TABLE"], errors="ignore")
    write_output_excel(output_df, output_path)

if __name__ == "__main__":
    main()
