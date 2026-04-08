from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogColumn, ColumnReference, Expression, QueryBlock, TableReference
from tool_gen.core.ast.parser import QueryBlockValidator

from ..errors import AmbiguousColumnError, AliasResolutionError, MissingColumnError, MissingTableError
from ..helpers.ast_utils import iter_columns, iter_selects

from .base import BaseStage


class ColumnReferenceStage(BaseStage):
    @staticmethod
    def resolve_clause_expressions(
        select: exp.Select,
    ) -> List[Tuple[str, str, List[exp.Expression]]]:
        clauses: List[Tuple[str, str, List[exp.Expression]]] = []
        if select.expressions:
            clauses.append(("PROJECTION", "PROJECTION", list(select.expressions)))
        where = select.args.get("where")
        if isinstance(where, exp.Where) and where.this is not None:
            clauses.append(("WHERE", "FILTER", [where.this]))
        having = select.args.get("having")
        if isinstance(having, exp.Having) and having.this is not None:
            clauses.append(("HAVING", "HAVING_CONDITION", [having.this]))
        joins = select.args.get("joins") or []
        join_exprs: List[exp.Expression] = []
        for join in joins:
            if not isinstance(join, exp.Join):
                continue
            join_on = join.args.get("on")
            if isinstance(join_on, exp.Expression):
                join_exprs.append(join_on)
        if join_exprs:
            clauses.append(("JOIN_ON", "JOIN_CONDITION", join_exprs))
        group = select.args.get("group")
        if isinstance(group, exp.Group) and group.expressions:
            clauses.append(("GROUP_BY", "GROUP_KEY", list(group.expressions)))
        order = select.args.get("order")
        if isinstance(order, exp.Order) and order.expressions:
            clauses.append(("ORDER_BY", "ORDER_KEY", list(order.expressions)))
        return clauses

    @staticmethod
    def normalized_sql(node: exp.Expression) -> str:
        raw = node.sql(pretty=False) if isinstance(node, exp.Expression) else ""
        return raw.strip().lower()

    def run(self) -> List[Any]:
        """Extract ColumnReference[] by clause and expression."""
        ast = self.context.ast
        node_to_block: Dict[int, QueryBlock] = self.context.node_to_block
        table_references: List[TableReference] = list(self.context.table_references)
        catalog_columns: List[CatalogColumn] = list(self.context.catalog_columns)
        expressions: List[Expression] = list(self.context.expressions)

        table_refs_by_block: Dict[int, List[TableReference]] = {}
        for ref in table_references:
            block = ref.query_block
            if block is None:
                continue
            table_refs_by_block.setdefault(id(block), []).append(ref)

        column_index: Dict[Tuple[int, str], CatalogColumn] = {}
        for column in catalog_columns:
            if column.table is None or not column.column_name:
                continue
            column_index[(id(column.table), column.column_name.lower())] = column

        expr_index: Dict[Tuple[int, str, str], Expression] = {}
        for expr in expressions:
            if expr.expression_type != "COLUMN":
                continue
            block = expr.query_block
            if block is None:
                continue
            role = (expr.expression_role or "").strip()
            sql = (expr.normalized_sql_text or expr.raw_sql_text or "").strip().lower()
            if role and sql:
                expr_index[(id(block), role, sql)] = expr

        results: List[ColumnReference] = []

        for select in iter_selects(ast):
            block = node_to_block.get(id(select))
            if block is None:
                continue

            refs = table_refs_by_block.get(id(block), [])
            alias_seen: Dict[str, int] = {}
            for ref in refs:
                alias = (ref.table_alias or "").strip().lower()
                if alias:
                    alias_seen[alias] = alias_seen.get(alias, 0) + 1
            if any(count > 1 for count in alias_seen.values()):
                raise AliasResolutionError("Duplicate table alias in block")

            for clause_type, role, exprs in self.resolve_clause_expressions(select):
                ordinal = 0
                for expr in exprs:
                    if not isinstance(expr, exp.Expression):
                        continue
                    for col in iter_columns(expr):
                        if bool(getattr(col, "is_star", False)):
                            continue
                        col_name = col.name
                        if not col_name:
                            continue
                        ordinal += 1

                        alias = (col.table or "").strip().lower()
                        if not alias and col_name.upper() in QueryBlockValidator.DEFAULT_ALLOWED_UNQUALIFIED:
                            table_ref = next((ref for ref in refs if ref.ref_type == "VARIABLE"), None)
                            if table_ref is None:
                                raise MissingTableError(
                                    f"Variable table not resolved for column: {col.sql(pretty=False)}"
                                )
                        else:
                            table_ref: TableReference | None = None
                            if alias:
                                for ref in refs:
                                    if (ref.table_alias or "").strip().lower() == alias:
                                        table_ref = ref
                                        break
                                if table_ref is None:
                                    for ref in refs:
                                        if (ref.source_sql_name or "").strip().lower() == alias:
                                            table_ref = ref
                                            break
                                if table_ref is None:
                                    raise MissingTableError(
                                        f"Table alias not resolved for column: {col.sql(pretty=False)}"
                                    )
                            else:
                                non_variable_refs = [ref for ref in refs if ref.ref_type != "VARIABLE"]
                                if len(non_variable_refs) == 1:
                                    table_ref = non_variable_refs[0]
                                else:
                                    raise AmbiguousColumnError(
                                        f"Unqualified column with multiple sources: {col.sql(pretty=False)}"
                                    )

                        column_obj = None
                        if table_ref is not None and table_ref.table is not None:
                            column_obj = column_index.get((id(table_ref.table), col_name.lower()))
                            if column_obj is None:
                                raise MissingColumnError(
                                    f"Column not found in catalog: {col_name}"
                                )

                        expression_obj = expr_index.get((id(block), role, self.normalized_sql(col)))

                        results.append(
                            ColumnReference(
                                query_block=block,
                                table_ref=table_ref,
                                column=column_obj,
                                expression=expression_obj,
                                column_name=col_name,
                                source_text=col.sql(pretty=False),
                                clause_type=clause_type,
                                ordinal_position=ordinal,
                            )
                        )

        return self.dedup_by_key(
            results,
            key_fn=lambda ref: (
                id(ref.query_block),
                ref.clause_type,
                id(ref.expression) if ref.expression is not None else None,
                ref.ordinal_position,
            ),
        )
