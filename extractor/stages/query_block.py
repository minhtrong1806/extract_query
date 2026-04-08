from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import QueryBlock

from ..helpers.ast_utils import iter_children
from ..helpers.resolve_utils import resolve_alias

from .base import BaseStage


class QueryBlockStage(BaseStage):
    @staticmethod
    def resolve_block_type(context_type: str | None, is_root: bool) -> str:
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
    def new_block(
        state: Dict[str, Any],
        *,
        parent: QueryBlock | None,
        block_type: str,
        alias_name: str | None,
        level: int,
        is_root: bool,
    ) -> QueryBlock:
        state["ordinal"] += 1
        return QueryBlock(
            mapping_id=None,
            parent=parent,
            block_type=block_type,
            alias_name=alias_name,
            level_no=level,
            ordinal_position=state["ordinal"],
            is_root=1 if is_root else 0,
        )

    @staticmethod
    def visit(
        state: Dict[str, Any],
        node: exp.Expression | None,
        parent: QueryBlock | None,
        level: int,
        context_type: str | None,
        alias_name: str | None,
        is_root: bool,
        blocks: List[QueryBlock],
        node_to_block: Dict[int, QueryBlock],
    ) -> None:
        if node is None:
            return

        if isinstance(node, exp.Subquery):
            QueryBlockStage.visit(
                state,
                node.this,
                parent,
                level,
                "SUBQUERY",
                resolve_alias(node),
                False,
                blocks,
                node_to_block,
            )
            return

        if isinstance(node, exp.Union):
            QueryBlockStage.visit(
                state,
                node.this,
                parent,
                level,
                "UNION_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
            )
            QueryBlockStage.visit(
                state,
                node.expression,
                parent,
                level,
                "UNION_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
            )
            return

        if isinstance(node, exp.Intersect):
            QueryBlockStage.visit(
                state,
                node.this,
                parent,
                level,
                "INTERSECT_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
            )
            QueryBlockStage.visit(
                state,
                node.expression,
                parent,
                level,
                "INTERSECT_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
            )
            return

        if isinstance(node, exp.Except):
            QueryBlockStage.visit(
                state,
                node.this,
                parent,
                level,
                "EXCEPT_LEFT",
                None,
                is_root,
                blocks,
                node_to_block,
            )
            QueryBlockStage.visit(
                state,
                node.expression,
                parent,
                level,
                "EXCEPT_RIGHT",
                None,
                False,
                blocks,
                node_to_block,
            )
            return

        if isinstance(node, exp.CTE):
            QueryBlockStage.visit(
                state,
                node.this,
                parent,
                level,
                "CTE",
                resolve_alias(node),
                False,
                blocks,
                node_to_block,
            )
            return

        if isinstance(node, exp.Select):
            if is_root and state["root_created"]:
                is_root = False
            elif is_root:
                state["root_created"] = True

            block_type = QueryBlockStage.resolve_block_type(context_type, is_root)
            block = QueryBlockStage.new_block(
                state,
                parent=parent,
                block_type=block_type,
                alias_name=alias_name,
                level=level,
                is_root=is_root,
            )
            blocks.append(block)
            node_to_block[id(node)] = block
            parent = block
            level += 1
            is_root = False

        for child in iter_children(node):
            QueryBlockStage.visit(state, child, parent, level, None, None, False, blocks, node_to_block)

    def run(self) -> Tuple[List[Any], Dict[Any, Any]]:
        """Create QueryBlock[] and node_to_block mapping."""
        ast = self.context.ast
        node_to_block: Dict[int, QueryBlock] = {}
        blocks: List[QueryBlock] = []
        if isinstance(ast, exp.Expression):
            state: Dict[str, Any] = {"ordinal": 0, "root_created": False}
            self.visit(state, ast, None, 0, None, None, True, blocks, node_to_block)

        return blocks, node_to_block
