from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogColumn, ColumnLineage, Expression, Projection, QueryBlock, TableReference
from tool_gen.core.ast.parser import QueryBlockValidator

from ..errors import AmbiguousColumnError, MissingColumnError, MissingTableError
from ..helpers.ast_utils import iter_selects

from .base import BaseStage


class ColumnLineageStage(BaseStage):
    @staticmethod
    def is_aggregate_expression(expression: exp.Expression) -> bool:
        return any(isinstance(node, exp.AggFunc) for node in expression.walk())

    @staticmethod
    def is_conditional_expression(expression: exp.Expression) -> bool:
        return any(isinstance(node, (exp.Case, exp.If)) for node in expression.walk())

    @staticmethod
    def resolve_lineage_type(
        expression: exp.Expression,
        source_columns: List[exp.Column],
    ) -> str:
        if not source_columns:
            return "UNKNOWN"
        if ColumnLineageStage.is_aggregate_expression(expression):
            return "AGGREGATED"
        if ColumnLineageStage.is_conditional_expression(expression):
            return "CONDITIONAL"
        if isinstance(expression, exp.Alias) and isinstance(expression.this, exp.Column):
            return "DIRECT"
        if isinstance(expression, exp.Column):
            return "DIRECT"
        return "DERIVED"

    @staticmethod
    def pop_source_expression(
        block: QueryBlock,
        column: exp.Column,
        source_expr_pool: Dict[Tuple[int, str], List[Expression]],
    ) -> Expression | None:
        sql = column.sql(pretty=False)
        normalized = sql.lower() if sql else ""
        pool = source_expr_pool.get((id(block), normalized))
        if not pool:
            return None
        return pool.pop(0)

    @staticmethod
    def find_table_ref(
        block: QueryBlock,
        column: exp.Column,
        table_refs_by_block: Dict[int, List[TableReference]],
    ) -> TableReference | None:
        refs = table_refs_by_block.get(id(block), [])
        if not refs:
            return None
        alias = (column.table or "").strip().lower()
        if alias:
            for ref in refs:
                if (ref.table_alias or "").strip().lower() == alias:
                    return ref
            for ref in refs:
                if (ref.source_sql_name or "").strip().lower() == alias:
                    return ref
            return None
        if (column.name or "").upper() in QueryBlockValidator.DEFAULT_ALLOWED_UNQUALIFIED:
            for ref in refs:
                if ref.ref_type == "VARIABLE":
                    return ref
        non_variable_refs = [ref for ref in refs if ref.ref_type != "VARIABLE"]
        if len(non_variable_refs) == 1:
            return non_variable_refs[0]
        raise AmbiguousColumnError(
            f"Unqualified column with multiple sources: {column.sql(pretty=False)}"
        )

    @staticmethod
    def resolve_source_column(
        block: QueryBlock,
        column: exp.Column,
        table_refs_by_block: Dict[int, List[TableReference]],
        column_index: Dict[Tuple[int, str], CatalogColumn],
    ) -> CatalogColumn | None:
        table_ref = ColumnLineageStage.find_table_ref(block, column, table_refs_by_block)
        if table_ref is None:
            raise MissingTableError(
                f"Table not resolved for column: {column.sql(pretty=False)}"
            )
        if table_ref.table is None:
            return None
        column_name = (column.name or "").strip().lower()
        if not column_name:
            return None
        column_obj = column_index.get((id(table_ref.table), column_name))
        if column_obj is None:
            raise MissingColumnError(f"Column not found in catalog: {column.name}")
        return column_obj

    def run(self) -> List[Any]:
        """Extract ColumnLineage[] between projections and sources."""
        ast = self.context.ast
        node_to_block: Dict[int, QueryBlock] = self.context.node_to_block
        projections: List[Projection] = list(self.context.projections)
        expressions: List[Expression] = list(self.context.expressions)
        table_references: List[TableReference] = list(self.context.table_references)
        catalog_columns: List[CatalogColumn] = list(self.context.catalog_columns)
        mapping = self.context.catalog_mapping

        projection_index: Dict[Tuple[int, int], Projection] = {}
        for proj in projections:
            if proj.query_block is None or proj.ordinal_position is None:
                continue
            projection_index[(id(proj.query_block), proj.ordinal_position)] = proj

        projection_expr_index: Dict[Tuple[int, int], Expression] = {}
        for expr in expressions:
            if expr.expression_role != "PROJECTION" or expr.level_no != 0:
                continue
            if expr.query_block is None or expr.ordinal_position is None:
                continue
            projection_expr_index[(id(expr.query_block), expr.ordinal_position)] = expr

        source_expr_pool: Dict[Tuple[int, str], List[Expression]] = {}
        for expr in expressions:
            if expr.expression_role != "PROJECTION" or expr.expression_type != "COLUMN":
                continue
            if expr.query_block is None:
                continue
            sql = (expr.normalized_sql_text or "").strip()
            if not sql:
                continue
            key = (id(expr.query_block), sql)
            source_expr_pool.setdefault(key, []).append(expr)

        table_refs_by_block: Dict[int, List[TableReference]] = {}
        for ref in table_references:
            if ref.query_block is None:
                continue
            table_refs_by_block.setdefault(id(ref.query_block), []).append(ref)

        column_index: Dict[Tuple[int, str], CatalogColumn] = {}
        for column in catalog_columns:
            if column.table is None or not column.column_name:
                continue
            column_index[(id(column.table), column.column_name.lower())] = column

        lineage: Dict[Tuple[int, int], ColumnLineage] = {}

        for select in iter_selects(ast):
            block = node_to_block.get(id(select))
            if block is None:
                continue
            for projection_ordinal, projection_ast in enumerate(select.expressions or [], start=1):
                projection = projection_index.get((id(block), projection_ordinal))
                root_expression = projection_expr_index.get((id(block), projection_ordinal))
                if projection is None or root_expression is None:
                    continue
                if projection.is_star:
                    continue

                source_columns = [
                    col
                    for col in projection_ast.find_all(exp.Column)
                    if not bool(getattr(col, "is_star", False))
                ]
                lineage_type = self.resolve_lineage_type(projection_ast, source_columns)

                source_expressions: List[Expression] = []
                source_column_objs: List[CatalogColumn | None] = []
                for source_column in source_columns:
                    source_expr = self.pop_source_expression(block, source_column, source_expr_pool)
                    if source_expr is not None:
                        source_expressions.append(source_expr)
                        source_column_objs.append(
                            self.resolve_source_column(
                                block,
                                source_column,
                                table_refs_by_block,
                                column_index,
                            )
                        )

                if not source_expressions:
                    source_expressions = [root_expression]
                    source_column_objs = [None]

                tgt_column = projection.output_column
                for src_expr, src_col in zip(source_expressions, source_column_objs):
                    resolved_tgt = tgt_column
                    if resolved_tgt is None and lineage_type == "DIRECT" and src_col is not None:
                        resolved_tgt = src_col

                    key = (id(src_expr), id(projection))
                    if key in lineage:
                        continue
                    lineage[key] = ColumnLineage(
                        mapping=mapping,
                        src_column=src_col,
                        src_column_ref=src_expr,
                        tgt_column=resolved_tgt,
                        projection=projection,
                        transform_expression=root_expression if lineage_type != "DIRECT" else None,
                        lineage_type=lineage_type,
                    )

        return list(lineage.values())
