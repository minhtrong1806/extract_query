from __future__ import annotations

from typing import Dict, List

import logging

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

logger = logging.getLogger(__name__)


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
        Danh sách dict gồm SCHEMA/TABLE/COLUMN và lý do nếu không suy luận được.
    """
    results: List[dict[str, str]] = []
    global_cte_index = build_global_cte_index(ast)
    for select in iter_selects(ast):
        ctx = build_select_context(select, sql_text=sql_text, inherited_cte_index=global_cte_index)
        for expression in select.expressions or []:
            for column in iter_columns(expression):
                if not isinstance(column, exp.Column):
                    continue
                if column.parent_select is not select:
                    continue
                if bool(getattr(column, "is_star", False)):
                    continue
                column_name = _column_output_name(column, placeholder_map)
                results.extend(_resolve_column_rows(ctx, column, placeholder_map, column_name, logger=logger))
    return results
