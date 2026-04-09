from __future__ import annotations

from typing import Dict, List

import json
import logging
import os

from sqlglot import expressions as exp

from ast_utils import iter_columns, iter_selects, iter_tables

from .context import build_global_cte_index, build_select_context
from .formatting import (
    _column_output_name,
    _dedup_preserve_order,
    _expression_sql,
    _format_table_name,
    _format_where_sql,
    _is_star,
    _output_name,
)
from .resolvers import _extract_rows_from_select as _resolve_rows_from_select
from .resolvers import _resolve_column_rows
from .stages import CatalogColumnStage, CatalogTableStage, QueryBlockStage
from logger import get_logger

logger = get_logger(__name__, log_to_file=True, log_dir="./logs")


def _write_jsonl(file_path: str, records: List[dict]) -> None:
    if not records:
        return
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_column_list(ast: exp.Expression, placeholder_map: Dict[str, str]) -> str:
    """Trích xuất danh sách cột trong SELECT (projection)."""
    columns: List[str] = []
    for select in iter_selects(ast):
        for expression in select.expressions or []:
            if _is_star(expression):
                columns.append(_expression_sql(expression, placeholder_map, dialect="oracle"))
                continue
            name = _output_name(expression, placeholder_map)
            if name:
                columns.append(name)
    return ", ".join(_dedup_preserve_order(columns))


def extract_table_list(ast: exp.Expression, placeholder_map: Dict[str, str]) -> str:
    """Trích xuất danh sách bảng xuất hiện trong câu lệnh."""
    tables: List[str] = []
    for table in iter_tables(ast):
        name = _format_table_name(table)
        if name:
            tables.append(name)
    return ", ".join(_dedup_preserve_order(tables))


def extract_where_list(ast: exp.Expression, placeholder_map: Dict[str, str]) -> str:
    """Trích xuất danh sách điều kiện WHERE dưới dạng SQL thuần."""
    wheres: List[str] = []
    for select in iter_selects(ast):
        where = select.args.get("where")
        if isinstance(where, exp.Where) and where.this is not None:
            sql = _format_where_sql(where.this, placeholder_map, dialect="oracle")
            if sql:
                wheres.append(sql)
    return " | ".join(_dedup_preserve_order(wheres))


def extract_schema_list(ast: exp.Expression, placeholder_map: Dict[str, str]) -> str:
    """Trích xuất danh sách schema từ các bảng xuất hiện trong câu lệnh."""
    schemas: List[str] = []
    for table in iter_tables(ast):
        schema_name = table.db or table.catalog
        if schema_name:
            schemas.append(schema_name)
    return ", ".join(_dedup_preserve_order(schemas))


def _extract_rows_from_select(
    select: exp.Select,
    placeholder_map: Dict[str, str],
    target_column: str,
) -> List[dict[str, str]]:
    return _resolve_rows_from_select(select, placeholder_map, target_column)


def extract_schema_table_column_rows(
    ast: exp.Expression,
    placeholder_map: Dict[str, str],
    sql_text: str | None = None,
) -> List[dict[str, str]]:
    """Trích xuất danh sách dòng (SCHEMA, TABLE, COLUMN) theo từng cột.

    Trả về:
        Danh sách dict gồm SCHEMA/TABLE/COLUMN/CLAUSE và lý do nếu không suy luận được.
    """
    results: List[dict[str, str]] = []
    global_cte_index = build_global_cte_index(ast)

    logger.info("Bat dau trich xuat rows tu AST")

    query_block_stage = QueryBlockStage()
    query_blocks, node_to_block = query_block_stage.run(ast, placeholder_map)
    logger.info("QueryBlockStage hoan tat: %s blocks", len(query_blocks))

    catalog_table_stage = CatalogTableStage()
    catalog_tables, select_context_by_block = catalog_table_stage.run(
        ast,
        node_to_block,
        placeholder_map,
        sql_text,
        global_cte_index,
    )
    logger.info("CatalogTableStage hoan tat: %s tables", len(catalog_tables))

    catalog_column_stage = CatalogColumnStage()
    catalog_columns = catalog_column_stage.run(
        ast,
        node_to_block,
        select_context_by_block,
        placeholder_map,
    )
    logger.info("CatalogColumnStage hoan tat: %s columns", len(catalog_columns))

    base_output_dir = os.path.join(os.getcwd(), "output", "temp")
    _write_jsonl(os.path.join(base_output_dir, "query_blocks.jsonl"), query_blocks)
    _write_jsonl(os.path.join(base_output_dir, "catalog_tables.jsonl"), catalog_tables)
    _write_jsonl(os.path.join(base_output_dir, "catalog_columns.jsonl"), catalog_columns)
    logger.info("Da ghi stage output vao %s", base_output_dir)

    for column in catalog_columns:
        results.append(
            {
                "CATALOG": column.get("catalog_name", ""),
                "SCHEMA": column.get("schema_name", ""),
                "TABLE": column.get("table_name", ""),
                "COLUMN": column.get("column_name", ""),
                "REASON": column.get("reason", ""),
                "CLAUSE": column.get("clause_type", ""),
            }
        )

    logger.info("Hoan tat trich xuat: %s rows", len(results))

    return results
