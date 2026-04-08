from __future__ import annotations

from typing import Any, Optional

from sqlglot import expressions as exp


def resolve_alias(node: Any) -> Optional[str]:
    """Resolve alias_or_name -> alias.name -> name."""
    if node is None:
        return None
    alias = getattr(node, "alias_or_name", None)
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    alias_node = getattr(node, "alias", None)
    alias_name = getattr(alias_node, "name", None)
    if isinstance(alias_name, str) and alias_name.strip():
        return alias_name.strip()
    name = getattr(node, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def resolve_table(node: Any) -> Optional[str]:
    """Resolve source_sql_name for a table reference."""
    if isinstance(node, exp.Table):
        return node.name
    name = getattr(node, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def resolve_column(node: Any) -> Optional[str]:
    """Resolve column name for a column reference."""
    if isinstance(node, exp.Column):
        return node.name
    name = getattr(node, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None
