from __future__ import annotations

from typing import Dict, Iterable, List

from sqlglot import expressions as exp


def _dedup_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _is_star(expression: exp.Expression) -> bool:
    if isinstance(expression, exp.Star):
        return True
    if isinstance(expression, exp.Column):
        return bool(getattr(expression, "is_star", False))
    return False


def _restore_placeholders(text: str, placeholder_map: Dict[str, str]) -> str:
    if not text or not placeholder_map:
        return text
    for token, original in placeholder_map.items():
        text = text.replace(token, original)
    return text


def _expression_sql(
    expression: exp.Expression,
    placeholder_map: Dict[str, str],
    dialect: str = "oracle",
) -> str:
    sql = expression.sql(pretty=False, dialect=dialect)
    return _restore_placeholders(sql, placeholder_map)


def _output_name(expression: exp.Expression, placeholder_map: Dict[str, str]) -> str:
    if isinstance(expression, exp.Alias):
        alias = expression.alias
        if isinstance(alias, str) and alias.strip():
            return _restore_placeholders(alias, placeholder_map)
        alias_name = getattr(alias, "name", None)
        if isinstance(alias_name, str) and alias_name.strip():
            return _restore_placeholders(alias_name, placeholder_map)
    if isinstance(expression, exp.Column) and expression.name:
        return _restore_placeholders(expression.name, placeholder_map)
    return _expression_sql(expression, placeholder_map, dialect="oracle")


def _format_table_name(table: exp.Table) -> str | None:
    if not isinstance(table, exp.Table) or not table.name:
        return None
    # parts = [table.catalog, table.db, table.name]
    parts = [table.db or table.catalog, table.name]
    return ".".join([part for part in parts if part])


def _format_where_sql(
    expression: exp.Expression,
    placeholder_map: Dict[str, str],
    dialect: str = "oracle",
) -> str:
    if isinstance(expression, exp.Not):
        inner = expression.this
        like_types = [exp.Like]
        if hasattr(exp, "ILike"):
            like_types.append(exp.ILike)
        if hasattr(exp, "RLike"):
            like_types.append(exp.RLike)
        if hasattr(exp, "SimilarTo"):
            like_types.append(exp.SimilarTo)

        if isinstance(inner, tuple(like_types)):
            left = _expression_sql(inner.this, placeholder_map, dialect=dialect)
            right = _expression_sql(inner.expression, placeholder_map, dialect=dialect)
            if left and right:
                if hasattr(exp, "ILike") and isinstance(inner, exp.ILike):
                    operator = "ILIKE"
                elif hasattr(exp, "RLike") and isinstance(inner, exp.RLike):
                    operator = "RLIKE"
                elif hasattr(exp, "SimilarTo") and isinstance(inner, exp.SimilarTo):
                    operator = "SIMILAR TO"
                else:
                    operator = "LIKE"
                return f"{left} NOT {operator} {right}"

        if isinstance(inner, exp.In):
            left = _expression_sql(inner.this, placeholder_map, dialect=dialect)
            query = inner.args.get("query")
            expressions = inner.args.get("expressions")
            if query is not None:
                right = _expression_sql(query, placeholder_map, dialect=dialect)
            elif expressions:
                right = f"({', '.join(_expression_sql(expr, placeholder_map, dialect=dialect) for expr in expressions)})"
            else:
                right = ""
            if left and right:
                return f"{left} NOT IN {right}"

        if isinstance(inner, exp.Between):
            left = _expression_sql(inner.this, placeholder_map, dialect=dialect)
            low = inner.args.get("low")
            high = inner.args.get("high")
            symmetric = inner.args.get("symmetric")
            if left and low is not None and high is not None:
                symmetric_sql = " SYMMETRIC" if symmetric else ""
                low_sql = _expression_sql(low, placeholder_map, dialect=dialect)
                high_sql = _expression_sql(high, placeholder_map, dialect=dialect)
                return f"{left} NOT BETWEEN{symmetric_sql} {low_sql} AND {high_sql}"
    return _expression_sql(expression, placeholder_map, dialect=dialect)


def _column_output_name(column: exp.Column, placeholder_map: Dict[str, str]) -> str:
    if bool(getattr(column, "is_star", False)):
        return "*"
    name = column.name or column.sql(pretty=False)
    return _restore_placeholders(name, placeholder_map)
