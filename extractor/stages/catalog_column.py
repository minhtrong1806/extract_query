from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogColumn, CatalogTable
from tool_gen.core.ast.parser import QueryBlockValidator

from ..constants import VAR_TABLE_KEY
from ..errors import AmbiguousColumnError, MissingTableError
from ..helpers.ast_utils import iter_columns
from ..helpers.resolve_utils import resolve_alias

from .base import BaseStage


class CatalogColumnStage(BaseStage):
    @staticmethod
    def iter_select_sources(select: exp.Select) -> List[exp.Expression]:
        sources: List[exp.Expression] = []
        from_clause = select.args.get("from") or select.args.get("from_")
        if isinstance(from_clause, exp.From):
            if isinstance(from_clause.this, exp.Expression):
                sources.append(from_clause.this)
            if from_clause.expressions:
                sources.extend(from_clause.expressions)
        joins = select.args.get("joins") or []
        for join in joins:
            if isinstance(join, exp.Join) and isinstance(join.this, exp.Expression):
                sources.append(join.this)
        return sources

    @staticmethod
    def build_alias_map(
        select: exp.Select,
        table_index: Dict[Tuple[str, str | None, str | None], CatalogTable],
    ) -> Tuple[Dict[str, CatalogTable], set[str], bool]:
        alias_map: Dict[str, CatalogTable] = {}
        subquery_aliases: set[str] = set()
        has_subquery_source = False
        for source in CatalogColumnStage.iter_select_sources(select):
            if isinstance(source, exp.Subquery):
                has_subquery_source = True
                alias = resolve_alias(source)
                if alias:
                    subquery_aliases.add(alias.strip().lower())
                continue
            if not isinstance(source, exp.Table):
                continue
            key = (source.name, source.db or None, source.catalog or None)
            table = table_index.get(key)
            if table is None:
                continue
            alias = resolve_alias(source)
            if not alias:
                continue
            alias_key = alias.strip().lower()
            if alias_key in alias_map and alias_map[alias_key] is not table:
                raise AmbiguousColumnError(f"Duplicate table alias in block: {alias}")
            alias_map[alias_key] = table
        return alias_map, subquery_aliases, has_subquery_source

    @staticmethod
    def output_name(expression: exp.Expression) -> str | None:
        if isinstance(expression, exp.Alias):
            alias = expression.alias
            if isinstance(alias, str) and alias.strip():
                return alias.strip()
            alias_name = getattr(alias, "name", None)
            if isinstance(alias_name, str) and alias_name.strip():
                return alias_name.strip()
        if isinstance(expression, exp.Column) and expression.name:
            return expression.name
        sql = expression.sql(pretty=False)
        return sql.strip() if sql else None

    @staticmethod
    def source_table_keys(ast: exp.Expression | None) -> set[Tuple[str, str | None, str | None]]:
        keys: set[Tuple[str, str | None, str | None]] = set()
        if isinstance(ast, exp.Expression):
            for table in ast.find_all(exp.Table):
                if not table.name:
                    continue
                keys.add((table.name, table.db or None, table.catalog or None))
        return keys

    @staticmethod
    def resolve_target_table(
        ast: exp.Expression | None,
        catalog_tables: List[CatalogTable],
        var_table: CatalogTable | None,
    ) -> CatalogTable | None:
        source_keys = CatalogColumnStage.source_table_keys(ast)
        candidates = [
            table
            for table in catalog_tables
            if (table.table_name, table.schema_name, table.catalog_name) not in source_keys
        ]
        if var_table is not None:
            candidates = [table for table in candidates if table is not var_table]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def run(self) -> List[Any]:
        """Extract CatalogColumn[] for source/target columns."""
        ast = self.context.ast
        catalog_tables: List[CatalogTable] = list(self.context.catalog_tables)

        table_index: Dict[Tuple[str, str | None, str | None], CatalogTable] = {}
        for table in catalog_tables:
            key = (table.table_name, table.schema_name, table.catalog_name)
            table_index[key] = table

        var_table = table_index.get(VAR_TABLE_KEY)

        unique: Dict[Tuple[int, str], CatalogColumn] = {}

        if isinstance(ast, exp.Expression):
            for select in ast.find_all(exp.Select):
                if self.context.node_to_block and id(select) not in self.context.node_to_block:
                    continue

                alias_map, subquery_aliases, has_subquery_source = self.build_alias_map(
                    select,
                    table_index,
                )
                single_alias = next(iter(alias_map)) if len(alias_map) == 1 else None

                for col in iter_columns(select):
                    if col.parent_select is not select:
                        continue
                    if bool(getattr(col, "is_star", False)):
                        continue
                    column_name = col.name
                    if not column_name:
                        continue

                    alias = (col.table or "").strip().lower()
                    if not alias:
                        if column_name.upper() in QueryBlockValidator.DEFAULT_ALLOWED_UNQUALIFIED:
                            if var_table is None:
                                raise MissingTableError(
                                    f"Variable table not resolved for column: {col.sql(pretty=False)}"
                                )
                            key = (id(var_table), column_name.lower())
                            if key in unique:
                                continue
                            unique[key] = CatalogColumn(
                                table=var_table,
                                column_name=column_name,
                                data_type=None,
                            )
                            continue
                        if not single_alias:
                            if has_subquery_source and not alias_map:
                                continue
                            raise AmbiguousColumnError(
                                f"Unqualified column with multiple sources: {col.sql(pretty=False)}"
                            )
                        alias = single_alias

                    table = alias_map.get(alias)
                    if table is None:
                        if alias in subquery_aliases:
                            continue
                        raise MissingTableError(
                            f"Table not resolved for column: {col.sql(pretty=False)}"
                        )

                    key = (id(table), column_name.lower())
                    if key in unique:
                        continue
                    unique[key] = CatalogColumn(
                        table=table,
                        column_name=column_name,
                        data_type=None,
                    )

        target_table = self.resolve_target_table(ast, catalog_tables, var_table)
        if target_table is not None and isinstance(ast, exp.Expression):
            for select in ast.find_all(exp.Select):
                for expression in select.expressions or []:
                    if isinstance(expression, exp.Star) or (
                        isinstance(expression, exp.Column) and bool(getattr(expression, "is_star", False))
                    ):
                        continue
                    name = self.output_name(expression)
                    if not name:
                        continue
                    key = (id(target_table), name.lower())
                    if key in unique:
                        continue
                    unique[key] = CatalogColumn(
                        table=target_table,
                        column_name=name,
                        data_type=None,
                    )

        return list(unique.values())
