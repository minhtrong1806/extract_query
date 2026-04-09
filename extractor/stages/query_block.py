from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from ast_utils import iter_children

from ..context import _resolve_alias
from ..formatting import _expression_sql


class QueryBlockStage:
    """Tạo danh sách QueryBlock và map node->block theo thứ tự."""

    @staticmethod
    def _resolve_block_type(context_type: str | None, is_root: bool) -> str:
        valid_types = {
            "CTE",
            "SUBQUERY",
            "UNION_LEFT",
            "UNION_RIGHT",
            "INTERSECT_LEFT",
            "INTERSECT_RIGHT",
            "EXCEPT_LEFT",
            "EXCEPT_RIGHT",
        }
        if context_type in valid_types:
            return context_type
        return "ROOT_SELECT" if is_root else "SELECT"

    @staticmethod
    def _new_block(
        state: Dict[str, Any],
        *,
        parent_id: int | None,
        block_type: str,
        alias_name: str | None,
        level: int,
        is_root: bool,
        raw_sql: str,
    ) -> Dict[str, Any]:
        state["ordinal"] += 1
        return {
            "block_id": state["ordinal"],
            "parent_id": parent_id,
            "block_type": block_type,
            "alias_name": alias_name or "",
            "level_no": level,
            "ordinal_position": state["ordinal"],
            "is_root": 1 if is_root else 0,
            "raw_sql": raw_sql,
        }

    @classmethod
    def _visit(
        cls,
        state: Dict[str, Any],
        node: exp.Expression | None,
        parent_id: int | None,
        level: int,
        context_type: str | None,
        alias_name: str | None,
        is_root: bool,
        blocks: List[Dict[str, Any]],
        node_to_block: Dict[int, int],
        placeholder_map: Dict[str, str],
    ) -> None:
        if node is None:
            return

        if isinstance(node, exp.Subquery):
            cls._visit(
                state,
                node.this,
                parent_id,
                level,
                "SUBQUERY",
                _resolve_alias(node),
                False,
                blocks,
                node_to_block,
                placeholder_map,
            )
            return

        if isinstance(node, exp.Union):
            cls._visit(
                state,
                node.this,
                parent_id,
                level,
                "UNION_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
                placeholder_map,
            )
            cls._visit(
                state,
                node.expression,
                parent_id,
                level,
                "UNION_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
                placeholder_map,
            )
            return

        if isinstance(node, exp.Intersect):
            cls._visit(
                state,
                node.this,
                parent_id,
                level,
                "INTERSECT_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
                placeholder_map,
            )
            cls._visit(
                state,
                node.expression,
                parent_id,
                level,
                "INTERSECT_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
                placeholder_map,
            )
            return

        if isinstance(node, exp.Except):
            cls._visit(
                state,
                node.this,
                parent_id,
                level,
                "EXCEPT_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
                placeholder_map,
            )
            cls._visit(
                state,
                node.expression,
                parent_id,
                level,
                "EXCEPT_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
                placeholder_map,
            )
            return

        if isinstance(node, exp.CTE):
            cls._visit(
                state,
                node.this,
                parent_id,
                level,
                "CTE",
                _resolve_alias(node),
                False,
                blocks,
                node_to_block,
                placeholder_map,
            )
            return

        if isinstance(node, exp.Select):
            if is_root and state["root_created"]:
                is_root = False
            elif is_root:
                state["root_created"] = True

            block_type = cls._resolve_block_type(context_type, is_root)
            block = cls._new_block(
                state,
                parent_id=parent_id,
                block_type=block_type,
                alias_name=alias_name,
                level=level,
                is_root=is_root,
                raw_sql=_expression_sql(node, placeholder_map, dialect="oracle"),
            )
            blocks.append(block)
            node_to_block[id(node)] = block["block_id"]
            parent_id = block["block_id"]
            level += 1
            is_root = False

        for child in iter_children(node):
            cls._visit(state, child, parent_id, level, None, None, False, blocks, node_to_block, placeholder_map)

    def run(
        self,
        ast: exp.Expression,
        placeholder_map: Dict[str, str],
    ) -> Tuple[List[Dict[str, Any]], Dict[int, int]]:
        """Trả về danh sách QueryBlock và map node_to_block."""
        blocks: List[Dict[str, Any]] = []
        node_to_block: Dict[int, int] = {}
        if isinstance(ast, exp.Expression):
            state: Dict[str, Any] = {"ordinal": 0, "root_created": False}
            self._visit(state, ast, None, 0, None, None, True, blocks, node_to_block, placeholder_map)
        return blocks, node_to_block
