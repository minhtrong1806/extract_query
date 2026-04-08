from __future__ import annotations

from typing import Iterable, List

from sqlglot import expressions as exp

from ast_utils import iter_selects, iter_tables


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


def _output_name(expression: exp.Expression) -> str:
    if isinstance(expression, exp.Alias):
        alias = expression.alias
        if isinstance(alias, str) and alias.strip():
            return alias
        alias_name = getattr(alias, "name", None)
        if isinstance(alias_name, str) and alias_name.strip():
            return alias_name
    if isinstance(expression, exp.Column) and expression.name:
        return expression.name
    return expression.sql(pretty=False, dialect="oracle")


def _format_table_name(table: exp.Table) -> str | None:
    if not isinstance(table, exp.Table) or not table.name:
        return None
    parts = [table.catalog, table.db, table.name]
    return ".".join([part for part in parts if part])


def _format_where_sql(expression: exp.Expression, dialect: str = "oracle") -> str:
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
            left = inner.this.sql(pretty=False, dialect=dialect)
            right = inner.expression.sql(pretty=False, dialect=dialect)
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
            left = inner.this.sql(pretty=False, dialect=dialect)
            query = inner.args.get("query")
            expressions = inner.args.get("expressions")
            if query is not None:
                right = query.sql(pretty=False, dialect=dialect)
            elif expressions:
                right = f"({', '.join(expr.sql(pretty=False, dialect=dialect) for expr in expressions)})"
            else:
                right = ""
            if left and right:
                return f"{left} NOT IN {right}"

        if isinstance(inner, exp.Between):
            left = inner.this.sql(pretty=False, dialect=dialect)
            low = inner.args.get("low")
            high = inner.args.get("high")
            symmetric = inner.args.get("symmetric")
            if left and low is not None and high is not None:
                symmetric_sql = " SYMMETRIC" if symmetric else ""
                low_sql = low.sql(pretty=False, dialect=dialect)
                high_sql = high.sql(pretty=False, dialect=dialect)
                return f"{left} NOT BETWEEN{symmetric_sql} {low_sql} AND {high_sql}"
    return expression.sql(pretty=False, dialect=dialect)


def extract_column_list(ast: exp.Expression) -> str:
    """Trích xuất danh sách cột trong SELECT (projection)."""
    columns: List[str] = []
    for select in iter_selects(ast):
        for expression in select.expressions or []:
            if _is_star(expression):
                columns.append(expression.sql(pretty=False, dialect="oracle"))
                continue
            name = _output_name(expression)
            if name:
                columns.append(name)
    return ", ".join(_dedup_preserve_order(columns))


def extract_table_list(ast: exp.Expression) -> str:
    """Trích xuất danh sách bảng xuất hiện trong câu lệnh."""
    tables: List[str] = []
    for table in iter_tables(ast):
        name = _format_table_name(table)
        if name:
            tables.append(name)
    return ", ".join(_dedup_preserve_order(tables))


def extract_where_list(ast: exp.Expression) -> str:
    """Trích xuất danh sách điều kiện WHERE dưới dạng SQL thuần."""
    wheres: List[str] = []
    for select in iter_selects(ast):
        where = select.args.get("where")
        if isinstance(where, exp.Where) and where.this is not None:
            sql = _format_where_sql(where.this, dialect="oracle")
            if sql:
                wheres.append(sql)
    return " | ".join(_dedup_preserve_order(wheres))
