from __future__ import annotations

from typing import Dict, List

from sqlglot import expressions as exp

from ast_utils import iter_columns

from .context import (
    SelectContext,
    _collect_tables_from_expression,
    _resolve_single_table_from_subquery,
    _resolve_subquery_for_column,
    _resolve_table_for_column,
    _resolve_table_from_text_alias,
    build_select_context,
)
from .formatting import _output_name, _restore_placeholders


def _select_has_output(select: exp.Select, placeholder_map: Dict[str, str], target_column: str) -> bool:
    target_key = target_column.lower()
    for expression in select.expressions or []:
        output_name = _output_name(expression, placeholder_map)
        if output_name and output_name.lower() == target_key:
            return True
    return False


def _subquery_has_output(node: exp.Expression, placeholder_map: Dict[str, str], target_column: str) -> bool:
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Expression):
        return _subquery_has_output(node.this, placeholder_map, target_column)
    if isinstance(node, exp.Select):
        return _select_has_output(node, placeholder_map, target_column)
    if isinstance(node, exp.SetOperation):
        return (
            _subquery_has_output(node.this, placeholder_map, target_column)
            or _subquery_has_output(node.expression, placeholder_map, target_column)
        )
    return False


def _select_output_is_derived(select: exp.Select, placeholder_map: Dict[str, str], target_column: str) -> bool:
    target_key = target_column.lower()
    for expression in select.expressions or []:
        output_name = _output_name(expression, placeholder_map)
        if not output_name or output_name.lower() != target_key:
            continue
        inner = expression.this if isinstance(expression, exp.Alias) else expression
        return not any(isinstance(col, exp.Column) for col in iter_columns(inner))
    return False


def _subquery_output_is_derived(node: exp.Expression, placeholder_map: Dict[str, str], target_column: str) -> bool:
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Expression):
        return _subquery_output_is_derived(node.this, placeholder_map, target_column)
    if isinstance(node, exp.Select):
        return _select_output_is_derived(node, placeholder_map, target_column)
    if isinstance(node, exp.SetOperation):
        return (
            _subquery_output_is_derived(node.this, placeholder_map, target_column)
            or _subquery_output_is_derived(node.expression, placeholder_map, target_column)
        )
    return False


def _subquery_has_star(node: exp.Expression) -> bool:
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Expression):
        return _subquery_has_star(node.this)
    if isinstance(node, exp.Select):
        for expression in node.expressions or []:
            if isinstance(expression, exp.Star):
                return True
            if isinstance(expression, exp.Column) and bool(getattr(expression, "is_star", False)):
                return True
        return False
    if isinstance(node, exp.SetOperation):
        return _subquery_has_star(node.this) or _subquery_has_star(node.expression)
    return False


def _make_row(
    catalog_name: str,
    schema_name: str,
    table_name: str,
    column_name: str,
    reason: str,
    placeholder_map: Dict[str, str],
) -> dict[str, str]:
    def _upper(value: str) -> str:
        return value.upper() if value else value

    return {
        "CATALOG": _upper(_restore_placeholders(catalog_name, placeholder_map)),
        "SCHEMA": _upper(_restore_placeholders(schema_name, placeholder_map)),
        "TABLE": _upper(_restore_placeholders(table_name, placeholder_map)),
        "COLUMN": _upper(column_name),
        "REASON": reason,
    }


def _extract_rows_from_subquery(
    node: exp.Expression,
    placeholder_map: Dict[str, str],
    target_column: str,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[tuple[int, str]] | None = None,
) -> List[dict[str, str]]:
    if visited is None:
        visited = set()
    node_key = (id(node), target_column.lower())
    if node_key in visited:
        return []
    visited.add(node_key)
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Expression):
        return _extract_rows_from_subquery(node.this, placeholder_map, target_column, cte_index, visited)
    if isinstance(node, exp.Select):
        return _extract_rows_from_select(node, placeholder_map, target_column, cte_index=cte_index, visited=visited)
    if isinstance(node, exp.Union):
        left_rows = _extract_rows_from_subquery(node.this, placeholder_map, target_column, cte_index, visited)
        right_rows = _extract_rows_from_subquery(node.expression, placeholder_map, target_column, cte_index, visited)
        seen = set()
        merged: List[dict[str, str]] = []
        for item in left_rows + right_rows:
            key = (item.get("CATALOG"), item.get("SCHEMA"), item.get("TABLE"), item.get("COLUMN"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged
    if isinstance(node, exp.SetOperation):
        left_rows = _extract_rows_from_subquery(node.this, placeholder_map, target_column, cte_index, visited)
        right_rows = _extract_rows_from_subquery(node.expression, placeholder_map, target_column, cte_index, visited)
        seen = set()
        merged: List[dict[str, str]] = []
        for item in left_rows + right_rows:
            key = (item.get("CATALOG"), item.get("SCHEMA"), item.get("TABLE"), item.get("COLUMN"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged
    return []


def _extract_rows_from_any_subquery(
    subqueries: List[exp.Expression],
    placeholder_map: Dict[str, str],
    target_column: str,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[tuple[int, str]] | None = None,
) -> List[dict[str, str]]:
    if visited is None:
        visited = set()
    merged: List[dict[str, str]] = []
    seen = set()
    for subquery in subqueries:
        rows = _extract_rows_from_subquery(subquery, placeholder_map, target_column, cte_index, visited)
        for item in rows:
            key = (item.get("CATALOG"), item.get("SCHEMA"), item.get("TABLE"), item.get("COLUMN"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _extract_rows_from_nested_selects(
    node: exp.Expression,
    placeholder_map: Dict[str, str],
    target_column: str,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[tuple[int, str]] | None = None,
) -> List[dict[str, str]]:
    if not isinstance(node, exp.Expression):
        return []
    if visited is None:
        visited = set()
    merged: List[dict[str, str]] = []
    seen = set()
    for select in node.find_all(exp.Select):
        select_key = (id(select), target_column.lower())
        if select_key in visited:
            continue
        visited.add(select_key)
        rows = _extract_rows_from_select(
            select,
            placeholder_map,
            target_column,
            cte_index=cte_index,
            visited=visited,
        )
        for item in rows:
            key = (item.get("CATALOG"), item.get("SCHEMA"), item.get("TABLE"), item.get("COLUMN"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _resolve_column_rows(
    ctx: SelectContext,
    column: exp.Column,
    placeholder_map: Dict[str, str],
    column_name: str,
    logger=None,
    strict_unqualified: bool = False,
    policy: Dict[str, bool] | None = None,
    visited: set[tuple[int, str]] | None = None,
) -> List[dict[str, str]]:
    policy = policy or {}
    allow_text_alias = policy.get("allow_text_alias", True)
    allow_text_table = policy.get("allow_text_table", True)
    allow_first_table = policy.get("allow_first_table", True)
    allow_table_plus_subquery = policy.get("allow_table_plus_subquery", True)

    if "#" in column_name:
        return [_make_row("", "", "dual", column_name, "PLACEHOLDER_COLUMN", placeholder_map)]

    def _resolve_from_subquery(
        subquery_expr: exp.Expression,
        nested_target: str,
        *,
        allow_alias_fallback: bool,
    ) -> List[dict[str, str]]:
        subquery_rows = _extract_rows_from_subquery(
            subquery_expr,
            placeholder_map,
            nested_target,
            cte_index=ctx.cte_index,
            visited=visited,
        )
        if subquery_rows:
            if nested_target != column_name:
                return [
                    {
                        **row,
                        "COLUMN": column_name.upper() if column_name else column_name,
                    }
                    for row in subquery_rows
                ]
            return subquery_rows

        nested_rows = _extract_rows_from_nested_selects(
            subquery_expr,
            placeholder_map,
            nested_target,
            cte_index=ctx.cte_index,
            visited=visited,
        )
        if nested_rows:
            if nested_target != column_name:
                return [
                    {
                        **row,
                        "COLUMN": column_name.upper() if column_name else column_name,
                    }
                    for row in nested_rows
                ]
            return nested_rows

        if _subquery_has_star(subquery_expr):
            single_table = _resolve_single_table_from_subquery(subquery_expr, cte_index=ctx.cte_index)
            if single_table is not None:
                schema_name = single_table.db or single_table.catalog or ""
                table_name = single_table.name or ""
                return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "SUBQUERY_STAR_FALLBACK", placeholder_map)]
            collected_tables = _collect_tables_from_expression(subquery_expr, cte_index=ctx.cte_index)
            if collected_tables:
                table = collected_tables[0]
                schema_name = table.db or table.catalog or ""
                table_name = table.name or ""
                return [_make_row(table.catalog or "", schema_name, table_name, column_name, "SUBQUERY_STAR_FALLBACK", placeholder_map)]

        if _subquery_output_is_derived(subquery_expr, placeholder_map, nested_target):
            return [_make_row("", "", "dual", column_name, "DERIVED_COLUMN", placeholder_map)]

        single_table = _resolve_single_table_from_subquery(subquery_expr, cte_index=ctx.cte_index)
        if single_table is not None:
            schema_name = single_table.db or single_table.catalog or ""
            table_name = single_table.name or ""
            return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "", placeholder_map)]

        if _subquery_has_output(subquery_expr, placeholder_map, nested_target):
            return [_make_row("", "", "dual", column_name, "DERIVED_COLUMN", placeholder_map)]

        if allow_alias_fallback:
            alias_name = (column.table or "").strip()
            if alias_name:
                return [_make_row("", "", alias_name, column_name, "SUBQUERY_ALIAS_FALLBACK", placeholder_map)]

        return []

    if strict_unqualified and not (column.table or "").strip():
        return [_make_row("", "", "", column_name, "UNRESOLVED_TABLE", placeholder_map)]

    column_alias = (column.table or "").strip().lower()
    resolved_table = None
    if not (column_alias and column_alias in ctx.subquery_alias_map):
        resolved_table = _resolve_table_for_column(column, ctx.alias_map, ctx.tables)
    if resolved_table is not None:
        table_key = (resolved_table.name or "").strip().lower()
        if table_key and table_key in ctx.cte_index:
            nested_target = column.name or column_name
            resolved_rows = _resolve_from_subquery(
                ctx.cte_index[table_key],
                nested_target,
                allow_alias_fallback=False,
            )
            if resolved_rows:
                return resolved_rows
            single_table = _resolve_single_table_from_subquery(ctx.cte_index[table_key], cte_index=ctx.cte_index)
            if single_table is not None:
                schema_name = single_table.db or single_table.catalog or ""
                table_name = single_table.name or ""
                return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "CTE_SINGLE_TABLE_FALLBACK", placeholder_map)]
            collected_tables = _collect_tables_from_expression(ctx.cte_index[table_key], cte_index=ctx.cte_index)
            if collected_tables:
                table = collected_tables[0]
                schema_name = table.db or table.catalog or ""
                table_name = table.name or ""
                return [_make_row(table.catalog or "", schema_name, table_name, column_name, "CTE_ANY_TABLE_FALLBACK", placeholder_map)]
        schema_name = resolved_table.db or resolved_table.catalog or ""
        table_name = resolved_table.name or ""
        return [_make_row(resolved_table.catalog or "", schema_name, table_name, column_name, "", placeholder_map)]

    if not ctx.tables and len(ctx.fallback_tables) == 1:
        single = ctx.fallback_tables[0]
        table_key = (single.name or "").strip().lower()
        if table_key and table_key in ctx.cte_index:
            nested_target = column.name or column_name
            resolved_rows = _resolve_from_subquery(
                ctx.cte_index[table_key],
                nested_target,
                allow_alias_fallback=False,
            )
            if resolved_rows:
                return resolved_rows
            single_table = _resolve_single_table_from_subquery(ctx.cte_index[table_key], cte_index=ctx.cte_index)
            if single_table is not None:
                schema_name = single_table.db or single_table.catalog or ""
                table_name = single_table.name or ""
                return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "CTE_SINGLE_TABLE_FALLBACK", placeholder_map)]
            collected_tables = _collect_tables_from_expression(ctx.cte_index[table_key], cte_index=ctx.cte_index)
            if collected_tables:
                table = collected_tables[0]
                schema_name = table.db or table.catalog or ""
                table_name = table.name or ""
                return [_make_row(table.catalog or "", schema_name, table_name, column_name, "CTE_ANY_TABLE_FALLBACK", placeholder_map)]
        schema_name = single.db or single.catalog or ""
        table_name = single.name or ""
        return [_make_row(single.catalog or "", schema_name, table_name, column_name, "", placeholder_map)]

    resolved_subquery = _resolve_subquery_for_column(column, ctx.subquery_alias_map)
    if resolved_subquery is not None:
        nested_target = column.name or column_name
        allow_alias_fallback = True
        cte_nodes: set[exp.Expression] = set()
        if ctx.cte_index:
            cte_nodes = set(ctx.cte_index.values())
            if resolved_subquery in cte_nodes:
                allow_alias_fallback = False
        resolved_rows = _resolve_from_subquery(
            resolved_subquery,
            nested_target,
            allow_alias_fallback=allow_alias_fallback,
        )
        if resolved_rows:
            if _subquery_has_star(resolved_subquery):
                all_empty_reason = all(not (row.get("REASON") or "").strip() for row in resolved_rows)
                if all_empty_reason and resolved_subquery in cte_nodes:
                    cte_reason = "CTE_SINGLE_TABLE_FALLBACK" if len(ctx.subqueries) > 1 else "SUBQUERY_STAR_FALLBACK"
                    resolved_rows = [{**row, "REASON": cte_reason} for row in resolved_rows]
            return resolved_rows
        if resolved_subquery in cte_nodes:
            collected_tables = _collect_tables_from_expression(resolved_subquery, cte_index=ctx.cte_index)
            unique_tables = {
                (table.name, table.db or "", table.catalog or "")
                for table in collected_tables
                if table.name and table.name.strip().lower() != "dual"
            }
            if unique_tables:
                rows: List[dict[str, str]] = []
                for name, schema, catalog in unique_tables:
                    rows.append(
                        _make_row(
                            catalog or "",
                            schema or "",
                            name or "",
                            column_name,
                            "CTE_ANY_TABLE_FALLBACK",
                            placeholder_map,
                        )
                    )
                return rows

    if allow_table_plus_subquery and not (column.table or "").strip():
        if len(ctx.tables) == 1 and len(ctx.subqueries) == 1:
            nested_target = column.name or column_name
            resolved_rows = _resolve_from_subquery(
                ctx.subqueries[0],
                nested_target,
                allow_alias_fallback=False,
            )
            if resolved_rows:
                return resolved_rows
            only_table = ctx.tables[0]
            schema_name = only_table.db or only_table.catalog or ""
            table_name = only_table.name or ""
            return [_make_row(only_table.catalog or "", schema_name, table_name, column_name, "", placeholder_map)]

    if allow_text_alias:
        text_resolution = _resolve_table_from_text_alias(column, ctx.text_alias_map, ctx.text_tables)
        if text_resolution is not None:
            schema_name, table_name = text_resolution
            cte_key = (table_name or "").strip().lower()
            if cte_key and cte_key in ctx.cte_index:
                nested_target = column.name or column_name
                resolved_rows = _resolve_from_subquery(
                    ctx.cte_index[cte_key],
                    nested_target,
                    allow_alias_fallback=False,
                )
                if resolved_rows:
                    return resolved_rows
                single_table = _resolve_single_table_from_subquery(ctx.cte_index[cte_key], cte_index=ctx.cte_index)
                if single_table is not None:
                    schema_name = single_table.db or single_table.catalog or ""
                    table_name = single_table.name or ""
                    return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "CTE_SINGLE_TABLE_FALLBACK", placeholder_map)]
                collected_tables = _collect_tables_from_expression(ctx.cte_index[cte_key], cte_index=ctx.cte_index)
                if collected_tables:
                    unique_tables = {
                        (table.name, table.db or "", table.catalog or "")
                        for table in collected_tables
                        if table.name and table.name.strip().lower() != "dual"
                    }
                    if unique_tables:
                        return [
                            _make_row(catalog or "", schema or "", name or "", column_name, "CTE_ANY_TABLE_FALLBACK", placeholder_map)
                            for name, schema, catalog in unique_tables
                        ]
            if logger is not None:
                logger.debug(
                    "Trace fallback alias tu SQL text cho column '%s' (alias '%s') -> %s.%s",
                    column_name,
                    (column.table or "").strip(),
                    schema_name,
                    table_name,
                )
            return [_make_row("", schema_name, table_name, column_name, "TEXT_ALIAS_FALLBACK", placeholder_map)]

    if logger is not None:
        from .context import _summarize_alias_map, _summarize_subquery_aliases, _summarize_tables

        logger.debug(
            "Trace resolve that bai cho column '%s' (alias '%s'). tables=%s alias_map=%s subquery_aliases=%s has_subquery_source=%s",
            column_name,
            (column.table or "").strip(),
            _summarize_tables(ctx.tables),
            _summarize_alias_map(ctx.alias_map),
            _summarize_subquery_aliases(ctx.subquery_alias_map),
            ctx.has_subquery_source,
        )

    if not ctx.tables and len(ctx.subqueries) == 1:
        nested_target = column.name or column_name
        resolved_rows = _resolve_from_subquery(
            ctx.subqueries[0],
            nested_target,
            allow_alias_fallback=False,
        )
        if resolved_rows:
            return resolved_rows
        if _subquery_output_is_derived(ctx.subqueries[0], placeholder_map, nested_target):
            return [_make_row("", "", "dual", column_name, "DERIVED_COLUMN", placeholder_map)]
        single_table = _resolve_single_table_from_subquery(ctx.subqueries[0], cte_index=ctx.cte_index)
        if single_table is not None:
            schema_name = single_table.db or single_table.catalog or ""
            table_name = single_table.name or ""
            return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "", placeholder_map)]
        if _subquery_has_output(ctx.subqueries[0], placeholder_map, nested_target):
            return [_make_row("", "", "dual", column_name, "DERIVED_COLUMN", placeholder_map)]
        collected_tables = _collect_tables_from_expression(ctx.subqueries[0], cte_index=ctx.cte_index)
        if collected_tables:
            table = collected_tables[0]
            schema_name = table.db or table.catalog or ""
            table_name = table.name or ""
            return [_make_row(table.catalog or "", schema_name, table_name, column_name, "SUBQUERY_ANY_TABLE_FALLBACK", placeholder_map)]

    if not ctx.tables and len(ctx.subqueries) > 1:
        nested_target = column.name or column_name
        subquery_rows = _extract_rows_from_any_subquery(
            ctx.subqueries,
            placeholder_map,
            nested_target,
            cte_index=ctx.cte_index,
            visited=visited,
        )
        if subquery_rows:
            if nested_target != column_name:
                return [
                    {
                        **row,
                        "COLUMN": column_name.upper() if column_name else column_name,
                    }
                    for row in subquery_rows
                ]
            return subquery_rows
        collected_tables: List[exp.Table] = []
        for subquery in ctx.subqueries:
            collected_tables.extend(_collect_tables_from_expression(subquery, cte_index=ctx.cte_index))
        unique_tables = {
            (table.name, table.db or "", table.catalog or "")
            for table in collected_tables
            if table.name
        }
        if len(unique_tables) == 1 and collected_tables:
            table = collected_tables[0]
            schema_name = table.db or table.catalog or ""
            table_name = table.name or ""
            return [_make_row(table.catalog or "", schema_name, table_name, column_name, "SUBQUERY_SINGLE_TABLE_FALLBACK", placeholder_map)]
        if collected_tables:
            table = collected_tables[0]
            schema_name = table.db or table.catalog or ""
            table_name = table.name or ""
            return [_make_row(table.catalog or "", schema_name, table_name, column_name, "SUBQUERY_ANY_TABLE_FALLBACK", placeholder_map)]

    if not column.table and ctx.star_aliases:
        for star_alias in ctx.star_aliases:
            alias_key = star_alias.strip().lower()
            direct_table = ctx.alias_map.get(alias_key)
            if direct_table is not None:
                schema_name = direct_table.db or direct_table.catalog or ""
                table_name = direct_table.name or ""
                return [_make_row(direct_table.catalog or "", schema_name, table_name, column_name, "STAR_ALIAS_FALLBACK", placeholder_map)]
            star_subquery = ctx.subquery_alias_map.get(alias_key)
            if star_subquery is not None:
                subquery_rows = _extract_rows_from_subquery(
                    star_subquery,
                    placeholder_map,
                    column_name,
                    cte_index=ctx.cte_index,
                    visited=visited,
                )
                if subquery_rows:
                    return subquery_rows
                single_table = _resolve_single_table_from_subquery(star_subquery, cte_index=ctx.cte_index)
                if single_table is not None:
                    schema_name = single_table.db or single_table.catalog or ""
                    table_name = single_table.name or ""
                    return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "STAR_ALIAS_FALLBACK", placeholder_map)]

    if allow_first_table and not column.table and ctx.tables:
        primary = ctx.tables[0]
        schema_name = primary.db or primary.catalog or ""
        table_name = primary.name or ""
        return [_make_row(primary.catalog or "", schema_name, table_name, column_name, "FIRST_TABLE_FALLBACK", placeholder_map)]

    if allow_text_table and ctx.text_tables:
        schema_name, table_name = ctx.text_tables[0]
        cte_key = (table_name or "").strip().lower()
        if cte_key and cte_key in ctx.cte_index:
            nested_target = column.name or column_name
            resolved_rows = _resolve_from_subquery(
                ctx.cte_index[cte_key],
                nested_target,
                allow_alias_fallback=False,
            )
            if resolved_rows:
                return resolved_rows
            single_table = _resolve_single_table_from_subquery(ctx.cte_index[cte_key], cte_index=ctx.cte_index)
            if single_table is not None:
                schema_name = single_table.db or single_table.catalog or ""
                table_name = single_table.name or ""
                return [_make_row(single_table.catalog or "", schema_name, table_name, column_name, "CTE_SINGLE_TABLE_FALLBACK", placeholder_map)]
            collected_tables = _collect_tables_from_expression(ctx.cte_index[cte_key], cte_index=ctx.cte_index)
            if collected_tables:
                table = collected_tables[0]
                schema_name = table.db or table.catalog or ""
                table_name = table.name or ""
                return [_make_row(table.catalog or "", schema_name, table_name, column_name, "CTE_ANY_TABLE_FALLBACK", placeholder_map)]
        return [_make_row("", schema_name, table_name, column_name, "TEXT_TABLE_FALLBACK", placeholder_map)]

    reason = "UNRESOLVED_TABLE" if ctx.tables else "SUBQUERY_UNRESOLVED"
    return [_make_row("", "", "", column_name, reason, placeholder_map)]


def _extract_rows_from_select(
    select: exp.Select,
    placeholder_map: Dict[str, str],
    target_column: str,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[tuple[int, str]] | None = None,
) -> List[dict[str, str]]:
    ctx = build_select_context(select, inherited_cte_index=cte_index)
    resolved_rows: List[dict[str, str]] = []
    has_star = False
    target_key = target_column.lower()
    for expression in select.expressions or []:
        if isinstance(expression, exp.Star):
            has_star = True
        if isinstance(expression, exp.Column) and bool(getattr(expression, "is_star", False)):
            has_star = True
        output_name = _output_name(expression, placeholder_map)
        if not output_name or output_name.lower() != target_key:
            continue
        for column in iter_columns(expression):
            if not isinstance(column, exp.Column):
                continue
            if column.parent_select is not select:
                continue
            if bool(getattr(column, "is_star", False)):
                has_star = True
                continue
            source_column_name = _restore_placeholders(column.name or target_column, placeholder_map)
            rows = _resolve_column_rows(ctx, column, placeholder_map, source_column_name, visited=visited)
            if rows:
                resolved_rows.extend(rows)
    if resolved_rows:
        return resolved_rows
    if has_star and ctx.star_aliases:
        for star_alias in ctx.star_aliases:
            alias_key = star_alias.strip().lower()
            direct_table = ctx.alias_map.get(alias_key)
            if direct_table is not None:
                schema_name = direct_table.db or direct_table.catalog or ""
                table_name = direct_table.name or ""
                return [_make_row(direct_table.catalog or "", schema_name, table_name, target_column, "STAR_ALIAS_FALLBACK", placeholder_map)]
            star_subquery = ctx.subquery_alias_map.get(alias_key)
            if star_subquery is not None:
                nested_rows = _extract_rows_from_subquery(
                    star_subquery,
                    placeholder_map,
                    target_column,
                    cte_index=ctx.cte_index,
                    visited=visited,
                )
                if nested_rows:
                    return nested_rows
                single_table = _resolve_single_table_from_subquery(star_subquery, cte_index=ctx.cte_index)
                if single_table is not None:
                    schema_name = single_table.db or single_table.catalog or ""
                    table_name = single_table.name or ""
                    return [_make_row(single_table.catalog or "", schema_name, table_name, target_column, "STAR_ALIAS_FALLBACK", placeholder_map)]
    if has_star and ctx.subqueries:
        star_rows = _extract_rows_from_any_subquery(
            ctx.subqueries,
            placeholder_map,
            target_column,
            cte_index=ctx.cte_index,
            visited=visited,
        )
        if star_rows:
            return star_rows
    if has_star and len(ctx.tables) == 1:
        table = ctx.tables[0]
        schema_name = table.db or table.catalog or ""
        table_name = table.name or ""
        return [_make_row(table.catalog or "", schema_name, table_name, target_column, "", placeholder_map)]
    if has_star and not ctx.tables and ctx.subqueries:
        single_table = (
            _resolve_single_table_from_subquery(ctx.subqueries[0], cte_index=ctx.cte_index)
            if len(ctx.subqueries) == 1
            else None
        )
        if single_table is None:
            collected_tables: List[exp.Table] = []
            for subquery in ctx.subqueries:
                collected_tables.extend(_collect_tables_from_expression(subquery, cte_index=ctx.cte_index))
            unique_tables = {
                (table.name, table.db or "", table.catalog or "")
                for table in collected_tables
                if table.name
            }
            if len(unique_tables) == 1 and collected_tables:
                single_table = collected_tables[0]
        if single_table is not None:
            schema_name = single_table.db or single_table.catalog or ""
            table_name = single_table.name or ""
            return [_make_row(single_table.catalog or "", schema_name, table_name, target_column, "SUBQUERY_STAR_FALLBACK", placeholder_map)]
    return []
