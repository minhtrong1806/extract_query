from __future__ import annotations

from typing import Any, Iterable

from sqlglot import expressions as exp


def iter_children(node: Any) -> Iterable[Any]:
    """Duyệt các node con thông qua node.args (Expression và list)."""
    if not hasattr(node, "args"):
        return []
    for value in node.args.values():
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, exp.Expression):
                    yield item
        elif isinstance(value, exp.Expression):
            yield value


def iter_selects(ast: Any) -> Iterable[Any]:
    """Trả về các node SELECT qua find_all(exp.Select)."""
    if isinstance(ast, exp.Expression):
        yield from ast.find_all(exp.Select)
    return []


def iter_tables(ast: Any) -> Iterable[Any]:
    """Trả về các node TABLE qua find_all(exp.Table); chỉ lấy tên hợp lệ."""
    if isinstance(ast, exp.Expression):
        for table in ast.find_all(exp.Table):
            if getattr(table, "name", None):
                yield table
    return []


def iter_columns(node: Any) -> Iterable[Any]:
    """Trả về các node COLUMN qua find_all(exp.Column); bỏ qua STAR khi cần."""
    if isinstance(node, exp.Expression):
        for col in node.find_all(exp.Column):
            yield col
    return []
