from __future__ import annotations

import pandas as pd

from extractors import (
    extract_column_list,
    extract_schema_list,
    extract_table_list,
    extract_where_list,
)
from models import ParseResult
from parser import parse_select_statement


def _extract_from_parse_result(parse_result: ParseResult, extractor) -> str:
    if parse_result.ast is None:
        return ""
    return extractor(parse_result.ast, parse_result.placeholder_map)


def build_output_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Tạo DataFrame đầu ra với TABLE/COLUMN/WHERE/SCHEMA từ dữ liệu đầu vào."""
    working_df = df.copy()

    working_df["AST"] = working_df["SELECT_STATEMENT_CLEANED"].apply(parse_select_statement)
    select_mask = working_df["READ_MODE"].eq("Select")

    working_df["TABLE"] = ["" for _ in range(len(working_df))]
    working_df["COLUMN"] = ["" for _ in range(len(working_df))]
    working_df["WHERE"] = ["" for _ in range(len(working_df))]
    working_df["SCHEMA"] = ["" for _ in range(len(working_df))]

    working_df.loc[select_mask, "TABLE"] = working_df.loc[select_mask, "AST"].apply(
        lambda parse_result: _extract_from_parse_result(parse_result, extract_table_list)
    )
    working_df.loc[select_mask, "COLUMN"] = working_df.loc[select_mask, "AST"].apply(
        lambda parse_result: _extract_from_parse_result(parse_result, extract_column_list)
    )
    working_df.loc[select_mask, "WHERE"] = working_df.loc[select_mask, "AST"].apply(
        lambda parse_result: _extract_from_parse_result(parse_result, extract_where_list)
    )
    working_df.loc[select_mask, "SCHEMA"] = working_df.loc[select_mask, "AST"].apply(
        lambda parse_result: _extract_from_parse_result(parse_result, extract_schema_list)
    )

    columns = [
        # "PROJECT_NAME",
        # "JOB_NAME",
        # "FOLDER_PATH",
        # "DATA_CONNECTION",
        # "STAGE_TYPE",
        # "STAGE_NAME",
        # "READ_MODE",
        # "WRITE_MODE",
        # "SELECT_STATEMENT",
        "SCHEMA",
        "TABLE",
        "COLUMN",
    ]
    return working_df[columns]
