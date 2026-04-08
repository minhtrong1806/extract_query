from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogColumn, CatalogTable, Projection, QueryBlock

from ..helpers.ast_utils import iter_selects

from .base import BaseStage


class ProjectionStage(BaseStage):
    @staticmethod
    def is_star(expression: exp.Expression) -> bool:
        if isinstance(expression, exp.Star):
            return True
        if isinstance(expression, exp.Column):
            return bool(getattr(expression, "is_star", False))
        return False

    @staticmethod
    def output_name(expression: exp.Expression) -> str:
        if isinstance(expression, exp.Alias):
            alias = expression.alias
            if isinstance(alias, str) and alias.strip():
                return alias
            alias_name = getattr(alias, "name", None)
            if isinstance(alias_name, str) and alias_name.strip():
                return alias_name
        if isinstance(expression, exp.Column) and expression.name:
            return expression.name
        return expression.sql(pretty=False)

    @staticmethod
    def source_table_keys(ast: exp.Expression | None) -> set[Tuple[str, str | None, str | None]]:
        keys: set[Tuple[str, str | None, str | None]] = set()
        if isinstance(ast, exp.Expression):
            for table in ast.find_all(exp.Table):
                if table.name:
                    keys.add((table.name, table.db or None, table.catalog or None))
        return keys

    @staticmethod
    def resolve_target_table(
        ast: exp.Expression | None,
        catalog_tables: List[CatalogTable],
    ) -> CatalogTable | None:
        source_keys = ProjectionStage.source_table_keys(ast)
        candidates = [
            t
            for t in catalog_tables
            if (t.table_name, t.schema_name, t.catalog_name) not in source_keys
        ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def run(self) -> List[Any]:
        """Extract Projection[] from SELECT expressions."""
        ast = self.context.ast
        node_to_block: Dict[int, QueryBlock] = self.context.node_to_block
        catalog_tables: List[CatalogTable] = list(self.context.catalog_tables)
        catalog_columns: List[CatalogColumn] = list(self.context.catalog_columns)

        target_table = self.resolve_target_table(ast, catalog_tables)
        column_index: Dict[Tuple[int, str], CatalogColumn] = {}
        if target_table is not None:
            for column in catalog_columns:
                if column.table is not target_table:
                    continue
                if column.column_name:
                    column_index[(id(target_table), column.column_name.lower())] = column

        projections: List[Projection] = []
        for select in iter_selects(ast):
            block = node_to_block.get(id(select))
            if block is None:
                continue
            for idx, expression in enumerate(select.expressions or [], start=1):
                star = self.is_star(expression)
                name = self.output_name(expression)
                output_column = None
                if not star and target_table is not None and name:
                    output_column = column_index.get((id(target_table), name.lower()))
                projections.append(
                    Projection(
                        query_block=block,
                        output_column_name=name,
                        output_column=output_column,
                        ordinal_position=idx,
                        is_star=1 if star else 0,
                    )
                )

        return self.dedup_by_key(
            projections,
            key_fn=lambda proj: (id(proj.query_block), proj.ordinal_position),
        )
