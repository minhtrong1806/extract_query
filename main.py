from pathlib import Path
import logging

from io_excel import read_excel_data, write_output_excel
from pipeline import build_output_dataframe


def _merge_unique_where(values) -> str:
    """Gộp WHERE_CONDITION duy nhất, giữ thứ tự xuất hiện."""
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return " ; ".join(merged)
    
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
