from __future__ import annotations

import logging

import pandas as pd

from extractor import extract_schema_table_column_rows
from parser import parse_select_statement

logger = logging.getLogger(__name__)


def build_output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Tạo DataFrame đầu ra theo từng cột với SCHEMA/TABLE/COLUMN."""
    working_df = df.copy()
    working_df["AST"] = working_df["SELECT_STATEMENT_CLEANED"].apply(parse_select_statement)
    select_mask = working_df["READ_MODE"].eq("Select")

    rows: list[dict[str, str]] = []
    for row in working_df.loc[
        select_mask,
        ["AST", "SELECT_STATEMENT", "SELECT_STATEMENT_CLEANED"],
    ].itertuples(index=False):
        parse_result = row.AST
        raw_sql = row.SELECT_STATEMENT
        cleaned_sql = row.SELECT_STATEMENT_CLEANED
        if parse_result.ast is None:
            continue
        extracted_rows = extract_schema_table_column_rows(
            parse_result.ast,
            parse_result.placeholder_map,
            sql_text=cleaned_sql or raw_sql,
        )
        for extracted in extracted_rows:
            schema_name = extracted["SCHEMA"]
            table_name = extracted["TABLE"]
            column_name = extracted["COLUMN"]
            reason = extracted.get("REASON") or ""
            if not schema_name and not table_name:
                logger.warning(
                    "Khong suy luan duoc SCHEMA/TABLE cho COLUMN '%s'. Ly do: %s. SQL: %s",
                    column_name,
                    reason or "UNKNOWN",
                    raw_sql,
                )
            rows.append(
                {
                    "SCHEMA": schema_name,
                    "TABLE": table_name,
                    "COLUMN": column_name,
                    "SELECT_STATEMENT": raw_sql or cleaned_sql or "",
                }
            )

    output_df = pd.DataFrame(
        rows,
        columns=["SCHEMA", "TABLE", "COLUMN", "SELECT_STATEMENT"],
    )
    if output_df.empty:
        return output_df
    return output_df.sort_values(
        by=["SCHEMA", "TABLE", "COLUMN"],
        ascending=[True, True, True],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
