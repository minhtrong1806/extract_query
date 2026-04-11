from __future__ import annotations

import logging

import pandas as pd

from extractor import extract_schema_table_column_rows
from parser import parse_select_statement
from logger import get_logger

logger = get_logger(__name__, log_to_file=True, log_dir="./logs")


def _normalize_table_only(table_name: str) -> str:
    table = (table_name or "").strip()
    if not table:
        return table
    if "." in table:
        return table.split(".")[-1].strip()
    return table


def _split_table_dblink(table_name: str) -> tuple[str, str]:
    """Tách DBLINK khỏi tên bảng theo dạng TABLE@DBLINK."""
    table = (table_name or "").strip()
    if not table:
        return "", ""
    if "@" not in table:
        return table, ""
    base_table, dblink = table.split("@", 1)
    dblink = dblink.strip()
    return base_table.strip(), (f"@{dblink}" if dblink else "")


def build_output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Tạo DataFrame đầu ra theo từng cột với SCHEMA/TABLE/COLUMN."""
    logger.info("Bat dau build_output_dataframe: %s dong", len(df))
    working_df = df.copy()
    working_df["AST"] = working_df["SELECT_STATEMENT_CLEANED"].apply(parse_select_statement)
    select_mask = working_df["READ_MODE"].eq("Select")

    rows: list[dict[str, str]] = []
    for row in working_df.loc[
        select_mask,
        ["AST", "SELECT_STATEMENT", "SELECT_STATEMENT_CLEANED", "JOB_NAME"],
    ].itertuples(index=False):
        parse_result = row.AST
        raw_sql = row.SELECT_STATEMENT
        cleaned_sql = row.SELECT_STATEMENT_CLEANED
        job_name = row.JOB_NAME
        if parse_result.ast is None:
            logger.warning(
                "Khong parse duoc SELECT_STATEMENT. Loi: %s. JOB_NAME: %s. SQL: %s",
                parse_result.error or "UNKNOWN",
                job_name,
                raw_sql,
            )
            continue
        logger.info("Dang xu ly SELECT_STATEMENT")
        extracted_rows = extract_schema_table_column_rows(
            parse_result.ast,
            parse_result.placeholder_map,
            sql_text=cleaned_sql or raw_sql,
        )
        for extracted in extracted_rows:
            schema_name = extracted["SCHEMA"]
            table_name = extracted["TABLE"]
            table_name = _normalize_table_only(table_name)
            table_name, dblink = _split_table_dblink(table_name)
            column_name = extracted["COLUMN"]
            if isinstance(column_name, str) and column_name.upper().startswith("V_"):
                continue
            reason = extracted.get("REASON") or ""
            clause_sql = extracted.get("CLAUSE_SQL") or ""
            clause = clause_sql or (extracted.get("CLAUSE") or "")
            where_condition = extracted.get("WHERE_CONDITION") or ""
            if not schema_name and not table_name:
                logger.warning(
                    "Khong suy luan duoc SCHEMA/TABLE cho COLUMN '%s'. Ly do: %s. JOB_NAME: %s. SQL: %s",
                    column_name,
                    reason or "UNKNOWN",
                    job_name,
                    raw_sql,
                )
            rows.append(
                {
                    "SCHEMA": schema_name,
                    "TABLE": table_name,
                    "DBLINK": dblink,
                    "COLUMN": column_name,
                    "WHERE_CONDITION": where_condition,
                    "CLAUSE": clause,
                }
            )

    output_df = pd.DataFrame(
        rows,
        columns=["SCHEMA", "TABLE", "DBLINK", "COLUMN", "WHERE_CONDITION", "CLAUSE"],
    )
    if output_df.empty:
        logger.warning("Output rong sau khi trich xuat")
        return output_df
    logger.info("Sap xep output theo SCHEMA/TABLE/DBLINK/COLUMN")
    return output_df.sort_values(
        by=["SCHEMA", "TABLE", "DBLINK", "COLUMN"],
        ascending=[True, True, True, True],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
