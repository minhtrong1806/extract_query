from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import re

from sqlglot import expressions as exp


@dataclass
class SelectContext:
    alias_map: Dict[str, exp.Table]
    subquery_alias_map: Dict[str, exp.Expression]
    tables: List[exp.Table]
    subqueries: List[exp.Expression]
    star_aliases: List[str]
    has_subquery_source: bool
    text_alias_map: Dict[str, Tuple[str, str]]
    text_tables: List[Tuple[str, str]]
    fallback_tables: List[exp.Table]
    cte_index: Dict[str, exp.Expression]


def build_select_context(
    select: exp.Select,
    sql_text: str | None = None,
    text_alias_map: Dict[str, Tuple[str, str]] | None = None,
    text_tables: List[Tuple[str, str]] | None = None,
    inherited_cte_index: Dict[str, exp.Expression] | None = None,
) -> SelectContext:
    cte_index = _merge_cte_index(select, inherited_cte_index)
    alias_map, subquery_alias_map, tables, subqueries, has_subquery_source = _build_alias_context(
        select,
        cte_index=cte_index,
    )
    outer_alias_map, outer_subquery_alias_map = _collect_outer_alias_context(
        select,
        cte_index=cte_index,
    )
    for alias_key, table in outer_alias_map.items():
        alias_map.setdefault(alias_key, table)
    for alias_key, subquery in outer_subquery_alias_map.items():
        # Không để correlated context từ SELECT cha ghi đè alias bảng vật lý local.
        # Ví dụ: inner có alias bảng "A", outer có subquery alias "A".
        # Trường hợp này phải ưu tiên bảng local để resolve A.COL_X đúng.
        if alias_key in alias_map:
            continue
        subquery_alias_map.setdefault(alias_key, subquery)
    star_aliases = _collect_star_aliases(select)
    if text_alias_map is None:
        text_alias_map = _build_alias_map_from_sql(sql_text or "")
    if text_tables is None:
        text_tables = _extract_tables_from_sql(sql_text or "")
    fallback_tables = [
        table
        for table in select.find_all(exp.Table)
        if table.name and table.name.strip().lower() not in cte_index
    ]
    return SelectContext(
        alias_map=alias_map,
        subquery_alias_map=subquery_alias_map,
        tables=tables,
        subqueries=subqueries,
        star_aliases=star_aliases,
        has_subquery_source=has_subquery_source,
        text_alias_map=text_alias_map,
        text_tables=text_tables,
        fallback_tables=fallback_tables,
        cte_index=cte_index,
    )


def _collect_outer_alias_context(
    select: exp.Select,
    cte_index: Dict[str, exp.Expression] | None = None,
) -> tuple[Dict[str, exp.Table], Dict[str, exp.Expression]]:
    """Thu thập alias/subquery_alias từ các SELECT cha để hỗ trợ correlated subquery."""
    merged_alias_map: Dict[str, exp.Table] = {}
    merged_subquery_alias_map: Dict[str, exp.Expression] = {}
    visited: set[int] = set()

    parent_select = select.parent_select
    while isinstance(parent_select, exp.Select):
        node_id = id(parent_select)
        if node_id in visited:
            break
        visited.add(node_id)

        parent_alias_map, parent_subquery_alias_map, _tables, _subqueries, _has_subquery_source = _build_alias_context(
            parent_select,
            cte_index=cte_index,
        )

        for alias_key, table in parent_alias_map.items():
            merged_alias_map.setdefault(alias_key, table)
        for alias_key, subquery in parent_subquery_alias_map.items():
            merged_subquery_alias_map.setdefault(alias_key, subquery)

        parent_select = parent_select.parent_select

    return merged_alias_map, merged_subquery_alias_map


def build_global_cte_index(ast: exp.Expression | None) -> Dict[str, exp.Expression]:
    """Lấy index CTE toàn cục từ AST để dùng khi không có ngữ cảnh cha."""
    if not isinstance(ast, exp.Expression):
        return {}
    cte_index: Dict[str, exp.Expression] = {}
    for cte in ast.find_all(exp.CTE):
        alias = _resolve_alias(cte)
        inner = cte.this
        if alias and isinstance(inner, exp.Expression):
            cte_index[alias.strip().lower()] = inner
    return cte_index


def _summarize_table(table: exp.Table) -> str:
    schema = table.db or table.catalog or ""
    name = table.name or ""
    if schema and name:
        return f"{schema}.{name}"
    return name or schema


def _summarize_tables(tables: List[exp.Table]) -> List[str]:
    return [_summarize_table(table) for table in tables]


def _summarize_alias_map(alias_map: Dict[str, exp.Table]) -> Dict[str, str]:
    return {alias: _summarize_table(table) for alias, table in alias_map.items()}


def _summarize_subquery_aliases(subquery_alias_map: Dict[str, exp.Expression]) -> Dict[str, str]:
    return {alias: value.__class__.__name__ for alias, value in subquery_alias_map.items()}


def _iter_select_sources(select: exp.Select) -> List[exp.Expression]:
    """Lấy danh sách nguồn trong FROM/JOIN của một SELECT."""
    sources: List[exp.Expression] = []
    from_clause = select.args.get("from") or select.args.get("from_")
    if isinstance(from_clause, exp.From):
        if isinstance(from_clause.this, exp.Expression):
            sources.append(from_clause.this)
        if from_clause.expressions:
            sources.extend([item for item in from_clause.expressions if isinstance(item, exp.Expression)])
    joins = select.args.get("joins") or []
    for join in joins:
        if isinstance(join, exp.Join) and isinstance(join.this, exp.Expression):
            sources.append(join.this)
    return sources


def _resolve_alias(node: exp.Expression | None) -> str | None:
    """Resolve alias/name theo logic extractor_package."""
    if node is None:
        return None
    args = getattr(node, "args", {}) or {}

    alias_node = args.get("alias")
    if isinstance(alias_node, exp.TableAlias):
        alias_name = getattr(alias_node, "name", None)
        if isinstance(alias_name, str) and alias_name.strip():
            return alias_name.strip()
        alias_this = getattr(alias_node, "this", None)
        if isinstance(alias_this, exp.Identifier) and alias_this.name:
            return alias_this.name.strip()
    elif isinstance(alias_node, exp.Alias):
        alias_name = getattr(alias_node, "name", None)
        if isinstance(alias_name, str) and alias_name.strip():
            return alias_name.strip()
        alias_this = getattr(alias_node, "this", None)
        if isinstance(alias_this, exp.Identifier) and alias_this.name:
            return alias_this.name.strip()

    this = args.get("this")
    if isinstance(this, exp.Identifier) and this.name:
        return this.name.strip()
    if isinstance(this, str) and this.strip():
        return this.strip()
    name = args.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _build_cte_index(select: exp.Select) -> Dict[str, exp.Expression]:
    """Lấy index CTE: alias -> expression."""
    cte_index: Dict[str, exp.Expression] = {}
    with_clause = select.args.get("with")
    if isinstance(with_clause, exp.With):
        for cte in with_clause.expressions or []:
            if not isinstance(cte, exp.CTE):
                continue
            alias = _resolve_alias(cte)
            inner = cte.this
            if alias and isinstance(inner, exp.Expression):
                cte_index[alias.strip().lower()] = inner
    return cte_index


def _merge_cte_index(
    select: exp.Select,
    inherited_cte_index: Dict[str, exp.Expression] | None,
) -> Dict[str, exp.Expression]:
    local_cte_index = _build_cte_index(select)
    if not inherited_cte_index:
        return local_cte_index
    merged = dict(inherited_cte_index)
    merged.update(local_cte_index)
    return merged


def _build_alias_context(
    select: exp.Select,
    cte_index: Dict[str, exp.Expression] | None = None,
) -> tuple[
    Dict[str, exp.Table],
    Dict[str, exp.Expression],
    List[exp.Table],
    List[exp.Expression],
    bool,
]:
    """Xây alias map theo logic extractor_package.

    Trả về:
        - alias_map: alias -> Table
        - subquery_alias_map: alias -> Subquery
        - tables: danh sách Table nguồn
        - subqueries: danh sách Subquery nguồn
        - has_subquery_source: có subquery trong nguồn hay không
    """
    alias_map: Dict[str, exp.Table] = {}
    subquery_alias_map: Dict[str, exp.Expression] = {}
    tables: List[exp.Table] = []
    subqueries: List[exp.Expression] = []
    has_subquery_source = False
    cte_index = cte_index or _build_cte_index(select)

    for source in _iter_select_sources(select):
        if isinstance(source, exp.Subquery):
            has_subquery_source = True
            subqueries.append(source)
            alias = _resolve_alias(source)
            if alias:
                subquery_alias_map[alias.strip().lower()] = source
            continue
        if not isinstance(source, exp.Table):
            continue
        if not source.name:
            continue
        cte_name = source.name.strip().lower()
        if cte_name in cte_index:
            has_subquery_source = True
            inner = cte_index[cte_name]
            subqueries.append(inner)
            subquery_alias_map[cte_name] = inner
            alias = _resolve_alias(source)
            if alias:
                subquery_alias_map[alias.strip().lower()] = inner
            continue
        tables.append(source)
        table_name_key = source.name.lower()
        if table_name_key not in alias_map:
            alias_map[table_name_key] = source
        alias = _resolve_alias(source)
        if alias:
            alias_key = alias.strip().lower()
            if alias_key not in alias_map or alias_map[alias_key] is source:
                alias_map[alias_key] = source

    if not tables and not subqueries:
        for table in select.find_all(exp.Table):
            if not table.name:
                continue
            tables.append(table)
            table_name_key = table.name.lower()
            if table_name_key not in alias_map:
                alias_map[table_name_key] = table
            alias = _resolve_alias(table)
            if alias:
                alias_key = alias.strip().lower()
                if alias_key not in alias_map or alias_map[alias_key] is table:
                    alias_map[alias_key] = table

    return alias_map, subquery_alias_map, tables, subqueries, has_subquery_source


def _resolve_table_for_column(
    column: exp.Column,
    alias_map: Dict[str, exp.Table],
    tables: List[exp.Table],
) -> exp.Table | None:
    alias = (column.table or "").strip().lower()
    if alias:
        direct = alias_map.get(alias)
        if direct is not None:
            return direct
        if "." in alias:
            alias = alias.split(".")[-1]
            direct = alias_map.get(alias)
            if direct is not None:
                return direct
        if len(tables) == 1:
            return tables[0]
        for table in tables:
            table_name = (table.name or "").strip().lower()
            if table_name and table_name == alias:
                return table
    if len(tables) == 1:
        return tables[0]
    return None


def _resolve_subquery_for_column(
    column: exp.Column,
    subquery_alias_map: Dict[str, exp.Expression],
) -> exp.Expression | None:
    alias = (column.table or "").strip().lower()
    if not alias:
        return None
    return subquery_alias_map.get(alias)


def _extract_star_alias(expression: exp.Expression) -> str | None:
    if isinstance(expression, exp.Column) and bool(getattr(expression, "is_star", False)):
        alias = (expression.table or "").strip()
        return alias or None
    if isinstance(expression, exp.Star):
        target = expression.args.get("this") or getattr(expression, "this", None)
        if isinstance(target, exp.Identifier):
            return target.name
        if isinstance(target, exp.Column):
            return target.table or target.name
        if isinstance(target, str):
            return target
    return None


def _collect_star_aliases(select: exp.Select) -> List[str]:
    aliases: List[str] = []
    seen = set()
    for expression in select.expressions or []:
        if isinstance(expression, exp.Star) or (isinstance(expression, exp.Column) and bool(getattr(expression, "is_star", False))):
            alias = _extract_star_alias(expression)
            if not alias:
                continue
            key = alias.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            aliases.append(alias)
    return aliases


def _build_alias_map_from_sql(sql_text: str) -> Dict[str, Tuple[str, str]]:
    alias_map: Dict[str, Tuple[str, str]] = {}
    if not sql_text:
        return alias_map
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([\w\.\[\]\"]+)\s+(?:AS\s+)?(\w+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql_text):
        table_ref = match.group(1)
        alias = match.group(2)
        if not alias:
            continue
        schema, table = _parse_table_ref(table_ref)
        alias_map[alias.lower()] = (schema, table)
    return alias_map


def _extract_tables_from_sql(sql_text: str) -> List[Tuple[str, str]]:
    tables: List[Tuple[str, str]] = []
    if not sql_text:
        return tables
    pattern = re.compile(r"\b(?:FROM|JOIN)\s+([\w\.\[\]\"]+)", re.IGNORECASE)
    seen = set()
    for match in pattern.finditer(sql_text):
        table_ref = match.group(1)
        if not table_ref:
            continue
        if table_ref.lower() == "select":
            continue
        schema, table = _parse_table_ref(table_ref)
        key = (schema, table)
        if key in seen:
            continue
        seen.add(key)
        tables.append(key)
    return tables


def _parse_table_ref(table_ref: str) -> Tuple[str, str]:
    if not table_ref:
        return "", ""
    raw_parts = table_ref.split(".")
    parts = [_normalize_identifier(part) for part in raw_parts if part]
    if not parts:
        return "", ""
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1]


def _normalize_identifier(value: str) -> str:
    result = value.strip()
    if result.startswith("[") and result.endswith("]"):
        result = result[1:-1]
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    return result


def _resolve_table_from_text_alias(
    column: exp.Column,
    text_alias_map: Dict[str, Tuple[str, str]],
    text_tables: List[Tuple[str, str]],
) -> Tuple[str, str] | None:
    if not text_alias_map and not text_tables:
        return None
    alias = (column.table or "").strip().lower()
    if alias:
        direct = text_alias_map.get(alias)
        if direct is not None:
            return direct
        if "." in alias:
            alias = alias.split(".")[-1]
            direct = text_alias_map.get(alias)
            if direct is not None:
                return direct

    unique_tables = {
        (schema or "", table or "")
        for schema, table in text_alias_map.values()
        if table
    }
    for schema, table in text_tables:
        if table:
            unique_tables.add((schema or "", table or ""))
    if len(unique_tables) == 1:
        return next(iter(unique_tables))
    return None


def _collect_tables_from_expression(
    node: exp.Expression,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[int] | None = None,
) -> List[exp.Table]:
    if visited is None:
        visited = set()
    node_id = id(node)
    if node_id in visited:
        return []
    visited.add(node_id)
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Expression):
        return _collect_tables_from_expression(node.this, cte_index, visited)
    if isinstance(node, exp.SetOperation):
        left_tables = _collect_tables_from_expression(node.this, cte_index, visited)
        right_tables = _collect_tables_from_expression(node.expression, cte_index, visited)
        merged: List[exp.Table] = []
        seen = set()
        for table in left_tables + right_tables:
            key = (table.name, table.db or "", table.catalog or "")
            if key in seen:
                continue
            seen.add(key)
            merged.append(table)
        return merged
    if not isinstance(node, exp.Select):
        return []

    cte_index = cte_index or _build_cte_index(node)
    collected: List[exp.Table] = []
    seen = set()

    def _add_table(table: exp.Table) -> None:
        key = (table.name, table.db or "", table.catalog or "")
        if key in seen:
            return
        seen.add(key)
        collected.append(table)

    for source in _iter_select_sources(node):
        if isinstance(source, exp.Table) and source.name:
            cte_name = source.name.strip().lower()
            if cte_name in cte_index:
                nested = _collect_tables_from_expression(cte_index[cte_name], cte_index, visited)
                for table in nested:
                    _add_table(table)
            else:
                _add_table(source)
            continue
        if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Expression):
            nested = _collect_tables_from_expression(source.this, cte_index, visited)
            for table in nested:
                _add_table(table)
    return collected


def _resolve_single_table_from_subquery(
    subquery: exp.Expression,
    cte_index: Dict[str, exp.Expression] | None = None,
    visited: set[int] | None = None,
) -> exp.Table | None:
    if visited is None:
        visited = set()
    node_id = id(subquery)
    if node_id in visited:
        return None
    visited.add(node_id)
    if isinstance(subquery, exp.Subquery):
        inner = subquery.this
    else:
        inner = subquery
    if isinstance(inner, exp.SetOperation):
        left = _resolve_single_table_from_subquery(inner.this, cte_index, visited)
        right = _resolve_single_table_from_subquery(inner.expression, cte_index, visited)
        if left is not None and right is not None:
            if (left.name, left.db or "", left.catalog or "") == (right.name, right.db or "", right.catalog or ""):
                return left
        return None
    if not isinstance(inner, exp.Select):
        return None
    alias_map, _subquery_alias_map, tables, subqueries, _has_subquery_source = _build_alias_context(
        inner,
        cte_index=cte_index,
    )
    if len(tables) == 1:
        return tables[0]
    if not tables and len(subqueries) == 1:
        return _resolve_single_table_from_subquery(subqueries[0], cte_index, visited)
    if not tables and subqueries:
        collected_tables = []
        for sub in subqueries:
            collected_tables.extend(_collect_tables_from_expression(sub, cte_index))
        unique_tables = {
            (table.name, table.db or "", table.catalog or "")
            for table in collected_tables
            if table.name
        }
        if len(unique_tables) == 1 and collected_tables:
            return collected_tables[0]
    if alias_map:
        unique_tables = {
            (table.name, table.db or "", table.catalog or "")
            for table in alias_map.values()
            if table.name
        }
        if len(unique_tables) == 1:
            return next(iter(alias_map.values()))
    return None
