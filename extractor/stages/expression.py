from __future__ import annotations

from typing import Any, Iterable, List

from sqlglot import expressions as exp

from tool_gen.core.models import Expression, QueryBlock

from ..helpers.ast_utils import iter_children, iter_selects

from .base import BaseStage


class ExpressionStage(BaseStage):
    @staticmethod
    def expression_type(node: exp.Expression) -> str:
        if isinstance(node, exp.Subquery):
            return "SUBQUERY_EXPR"
        if isinstance(node, exp.Column):
            if bool(getattr(node, "is_star", False)):
                return "STAR"
            return "COLUMN"
        if isinstance(node, exp.Star):
            return "STAR"
        if isinstance(node, exp.Literal):
            return "LITERAL"
        if isinstance(node, (exp.Func, exp.Anonymous)):
            return "FUNCTION"
        if isinstance(node, exp.Unary):
            return "UNARY_OP"
        if isinstance(node, exp.Case):
            return "CASE_WHEN"
        if isinstance(node, (exp.Binary, exp.Condition)):
            return "BINARY_OP"
        return "AST"

    @staticmethod
    def resolve_join_type(join: exp.Join) -> str:
        side = str(join.args.get("side") or "").strip().upper()
        kind = str(join.args.get("kind") or "").strip().upper()
        method = str(join.args.get("method") or "").strip().upper()
        tokens = {side, kind, method}
        if "CROSS" in tokens:
            return "CROSS"
        if "LEFT" in tokens:
            return "LEFT"
        if "RIGHT" in tokens:
            return "RIGHT"
        if "FULL" in tokens:
            return "FULL"
        if "INNER" in tokens:
            return "INNER"
        return "INNER"

    @staticmethod
    def walk(
        node: exp.Expression,
        block: QueryBlock,
        role: str,
        parent: Expression | None,
        level: int,
        ordinal: int,
        out: list[Expression],
        join_type: str | None = None,
    ) -> None:
        if not isinstance(node, exp.Expression):
            return
        raw_sql = node.sql(pretty=False)
        normalized_sql = BaseStage.normalize_sql(raw_sql)

        current = Expression(
            query_block=block,
            parent=parent,
            expression_type=ExpressionStage.expression_type(node),
            expression_role=role,
            join_type=join_type,
            raw_sql_text=raw_sql,
            normalized_sql_text=normalized_sql,
            level_no=level,
            ordinal_position=ordinal,
        )
        out.append(current)

        for idx, child in enumerate(iter_children(node), start=1):
            ExpressionStage.walk(child, block, role, current, level + 1, idx, out, join_type)

    def run(self) -> List[Any]:
        """Extract Expression[] across clauses."""
        ast = self.context.ast
        node_to_block: dict[int, QueryBlock] = self.context.node_to_block

        expressions: List[Expression] = []
        for select in iter_selects(ast):
            block = node_to_block.get(id(select))
            if block is None:
                continue

            projection_count = 0
            for idx, expr in enumerate(select.expressions or [], start=1):
                self.walk(expr, block, "PROJECTION", None, 0, idx, expressions)
                projection_count = idx

            extra_ordinal = projection_count

            where = select.args.get("where")
            if isinstance(where, exp.Where) and where.this is not None:
                extra_ordinal += 1
                self.walk(where.this, block, "FILTER", None, 0, extra_ordinal, expressions)

            having = select.args.get("having")
            if isinstance(having, exp.Having) and having.this is not None:
                extra_ordinal += 1
                self.walk(having.this, block, "HAVING_CONDITION", None, 0, extra_ordinal, expressions)

            joins = select.args.get("joins") or []
            for join in joins:
                if not isinstance(join, exp.Join):
                    continue
                join_on = join.args.get("on")
                if join_on is None or not isinstance(join_on, exp.Expression):
                    continue
                extra_ordinal += 1
                self.walk(
                    join_on,
                    block,
                    "JOIN_CONDITION",
                    None,
                    0,
                    extra_ordinal,
                    expressions,
                    self.resolve_join_type(join),
                )

            group = select.args.get("group")
            if isinstance(group, exp.Group):
                for expr in group.expressions or []:
                    extra_ordinal += 1
                    self.walk(expr, block, "GROUP_KEY", None, 0, extra_ordinal, expressions)

            order = select.args.get("order")
            if isinstance(order, exp.Order):
                for expr in order.expressions or []:
                    extra_ordinal += 1
                    self.walk(expr, block, "ORDER_KEY", None, 0, extra_ordinal, expressions)

            windows = select.args.get("windows") or []
            if isinstance(windows, exp.Window):
                windows = [windows]
            if isinstance(windows, list):
                for window in windows:
                    if isinstance(window, exp.Expression):
                        extra_ordinal += 1
                        self.walk(window, block, "WINDOW_SPEC", None, 0, extra_ordinal, expressions)

        return self.dedup_by_key(
            expressions,
            key_fn=lambda expr: (id(expr.query_block), id(expr.parent), expr.ordinal_position),
        )
