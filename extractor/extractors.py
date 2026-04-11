from __future__ import annotations

from typing import Any, Dict, List

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


def _split_and_predicates(expression: exp.Expression) -> List[exp.Expression]:
    if isinstance(expression, exp.And):
        left = expression.this
        right = expression.expression
        parts: List[exp.Expression] = []
        if isinstance(left, exp.Expression):
            parts.extend(_split_and_predicates(left))
        if isinstance(right, exp.Expression):
            parts.extend(_split_and_predicates(right))
        return parts
    return [expression]


def _table_key(catalog_name: str, schema_name: str, table_name: str) -> tuple[str, str, str]:
    return (catalog_name.upper(), schema_name.upper(), table_name.upper())


def _pick_physical_row(rows: List[dict[str, str]]) -> dict[str, str] | None:
    for row in rows:
        table_name = (row.get("TABLE") or "").strip()
        if not table_name:
            continue
        if table_name.upper() == "DUAL":
            continue
        return row
    return None


def _should_resolve_condition_column(column: exp.Column, column_name: str) -> bool:
    if not (column.table or "").strip():
        return False
    return not column_name.upper().startswith("V_")


def _normalize_predicate_with_resolved_tables(
    predicate: exp.Expression,
    ctx: Any,
    placeholder_map: Dict[str, str],
    policy: Dict[str, bool],
) -> exp.Expression:
    def _transform(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        if bool(getattr(node, "is_star", False)):
            return node

        if not _column_belongs_to_select_context(node, ctx):
            return node

        column_name = _column_output_name(node, placeholder_map)
        if not _should_resolve_condition_column(node, column_name):
            return node

        rows = _resolve_column_rows(
            ctx,
            node,
            placeholder_map,
            column_name,
            policy=policy,
        )
        chosen = _pick_physical_row(rows)
        if not chosen:
            return node

        table_name = (chosen.get("TABLE") or "").strip()
        if not table_name:
            return node

        return exp.Column(
            this=exp.to_identifier(node.name or column_name),
            table=exp.to_identifier(table_name),
        )

    return predicate.copy().transform(_transform)


def _column_belongs_to_select_context(column: exp.Column, ctx: Any) -> bool:
    alias = (column.table or "").strip().lower()
    if not alias:
        return False

    if alias in ctx.alias_map or alias in ctx.subquery_alias_map or alias in ctx.cte_index:
        return True

    if "." in alias:
        alias = alias.split(".")[-1]
        if alias in ctx.alias_map or alias in ctx.subquery_alias_map or alias in ctx.cte_index:
            return True

    for table in ctx.tables:
        table_name = (table.name or "").strip().lower()
        if table_name and table_name == alias:
            return True

    return False


def _build_where_condition_map(
    ast: exp.Expression,
    node_to_block: Dict[int, int],
    select_context_by_block: Dict[int, Any],
    placeholder_map: Dict[str, str],
) -> Dict[int, Dict[tuple[str, str, str], str]]:
    policy = {
        "allow_text_alias": True,
        "allow_text_table": True,
        "allow_first_table": True,
        "allow_table_plus_subquery": True,
    }
    strict_policy = {
        "allow_text_alias": False,
        "allow_text_table": False,
        "allow_first_table": False,
        "allow_table_plus_subquery": False,
    }

    temp_map: Dict[int, Dict[tuple[str, str, str], List[str]]] = {}

    for select in iter_selects(ast):
        block_id = node_to_block.get(id(select))
        if block_id is None:
            continue
        ctx = select_context_by_block.get(block_id)
        if ctx is None:
            continue

        predicates: List[exp.Expression] = []

        where = select.args.get("where")
        if isinstance(where, exp.Where) and isinstance(where.this, exp.Expression):
            predicates.extend(_split_and_predicates(where.this))

        joins = select.args.get("joins") or []
        for join in joins:
            if not isinstance(join, exp.Join):
                continue
            join_on = join.args.get("on")
            if isinstance(join_on, exp.Expression):
                predicates.extend(_split_and_predicates(join_on))

        for predicate in predicates:
            normalized_predicate = _normalize_predicate_with_resolved_tables(
                predicate,
                ctx,
                placeholder_map,
                strict_policy,
            )
            predicate_sql = _format_where_sql(normalized_predicate, placeholder_map, dialect="oracle")
            if not predicate_sql:
                continue

            table_keys: set[tuple[str, str, str]] = set()
            for column in iter_columns(predicate):
                if not isinstance(column, exp.Column):
                    continue
                if bool(getattr(column, "is_star", False)):
                    continue

                column_name = _column_output_name(column, placeholder_map)
                if not _should_resolve_condition_column(column, column_name):
                    continue
                if not _column_belongs_to_select_context(column, ctx):
                    continue
                rows = _resolve_column_rows(
                    ctx,
                    column,
                    placeholder_map,
                    column_name,
                    policy=strict_policy,
                )
                for row in rows:
                    table_name = row.get("TABLE", "")
                    if not table_name:
                        continue
                    if table_name.upper() == "DUAL":
                        continue
                    table_keys.add(
                        _table_key(
                            row.get("CATALOG", ""),
                            row.get("SCHEMA", ""),
                            table_name,
                        )
                    )

            if not table_keys:
                continue

            block_bucket = temp_map.setdefault(block_id, {})
            for key in table_keys:
                conditions = block_bucket.setdefault(key, [])
                if predicate_sql not in conditions:
                    conditions.append(predicate_sql)

    result: Dict[int, Dict[tuple[str, str, str], str]] = {}
    for block_id, table_conditions in temp_map.items():
        result[block_id] = {
            key: " AND ".join(values)
            for key, values in table_conditions.items()
        }
    return result


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

    where_condition_map = _build_where_condition_map(
        ast,
        node_to_block,
        select_context_by_block,
        placeholder_map,
    )

    base_output_dir = os.path.join(os.getcwd(), "output", "temp")
    _write_jsonl(os.path.join(base_output_dir, "query_blocks.jsonl"), query_blocks)
    _write_jsonl(os.path.join(base_output_dir, "catalog_tables.jsonl"), catalog_tables)
    _write_jsonl(os.path.join(base_output_dir, "catalog_columns.jsonl"), catalog_columns)
    logger.info("Da ghi stage output vao %s", base_output_dir)

    for column in catalog_columns:
        block_id = column.get("block_id")
        table_key = _table_key(
            column.get("catalog_name", ""),
            column.get("schema_name", ""),
            column.get("table_name", ""),
        )
        where_condition = ""
        if isinstance(block_id, int):
            where_condition = where_condition_map.get(block_id, {}).get(table_key, "")

        results.append(
            {
                "CATALOG": column.get("catalog_name", ""),
                "SCHEMA": column.get("schema_name", ""),
                "TABLE": column.get("table_name", ""),
                "COLUMN": column.get("column_name", ""),
                "REASON": column.get("reason", ""),
                "CLAUSE": column.get("clause_type", ""),
                "CLAUSE_SQL": column.get("clause_sql", ""),
                "WHERE_CONDITION": where_condition,
            }
        )

    logger.info("Hoan tat trich xuat: %s rows", len(results))

    return results
