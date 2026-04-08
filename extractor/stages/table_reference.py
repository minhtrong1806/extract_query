from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogTable, QueryBlock, TableReference
from tool_gen.core.ast.parser import QueryBlockValidator

from ..constants import VAR_TABLE_KEY
from ..errors import MissingTableError
from ..helpers.ast_utils import iter_columns, iter_selects
from ..helpers.resolve_utils import resolve_alias

from .base import BaseStage


class TableReferenceStage(BaseStage):
    @staticmethod
    def build_cte_index(
        select: exp.Select,
        node_to_block: Dict[int, QueryBlock],
    ) -> Dict[str, QueryBlock]:
        cte_index: Dict[str, QueryBlock] = {}
        with_clause = select.args.get("with")
        if isinstance(with_clause, exp.With):
            for cte in with_clause.expressions:
                if not isinstance(cte, exp.CTE):
                    continue
                alias = resolve_alias(cte)
                inner = cte.this
                if not alias or not isinstance(inner, exp.Expression):
                    continue
                block = node_to_block.get(id(inner))
                if block is not None:
                    cte_index[alias.lower()] = block
        return cte_index

    @staticmethod
    def iter_sources(select: exp.Select) -> List[Tuple[exp.Expression, bool]]:
        sources: List[Tuple[exp.Expression, bool]] = []
        from_clause = select.args.get("from") or select.args.get("from_")
        if isinstance(from_clause, exp.From):
            if isinstance(from_clause.this, exp.Expression):
                sources.append((from_clause.this, True))
            if from_clause.expressions:
                sources.extend([(item, True) for item in from_clause.expressions])
        joins = select.args.get("joins") or []
        for join in joins:
            if isinstance(join, exp.Join) and isinstance(join.this, exp.Expression):
                sources.append((join.this, False))
        return sources

    def run(self) -> List[Any]:
        """Extract TableReference[] from FROM/JOIN."""
        ast = self.context.ast
        node_to_block: Dict[int, QueryBlock] = self.context.node_to_block
        catalog_tables: List[CatalogTable] = list(self.context.catalog_tables)

        table_index: Dict[Tuple[str, str | None, str | None], CatalogTable] = {}
        for table in catalog_tables:
            table_index[(table.table_name, table.schema_name, table.catalog_name)] = table

        var_table = table_index.get(VAR_TABLE_KEY)

        references: List[TableReference] = []
        for select in iter_selects(ast):
            block = node_to_block.get(id(select))
            if block is None:
                continue

            cte_index = self.build_cte_index(select, node_to_block)
            ordinal = 0
            base_sources = self.iter_sources(select)
            for source, is_base in base_sources:
                ordinal += 1
                if isinstance(source, exp.Subquery):
                    referenced = node_to_block.get(id(source.this))
                    if referenced is None:
                        raise MissingTableError("Referenced subquery block not found")
                    references.append(
                        TableReference(
                            query_block=block,
                            ref_type="SUBQUERY",
                            referenced_query_block=referenced,
                            table_alias=resolve_alias(source),
                            source_sql_name=resolve_alias(source),
                            ordinal_position=ordinal,
                            is_base_source=1 if is_base else 0,
                        )
                    )
                    continue

                if isinstance(source, exp.Table):
                    table_name = source.name
                    alias = resolve_alias(source)
                    if table_name:
                        cte_block = cte_index.get(table_name.lower())
                        if cte_block is not None:
                            references.append(
                                TableReference(
                                    query_block=block,
                                    ref_type="CTE",
                                    referenced_query_block=cte_block,
                                    table_alias=alias,
                                    source_sql_name=table_name,
                                    ordinal_position=ordinal,
                                    is_base_source=1 if is_base else 0,
                                )
                            )
                            continue

                    table = table_index.get((table_name, source.db or None, source.catalog or None))
                    if table is None:
                        raise MissingTableError(
                            f"Physical table not found in catalog: {table_name}"
                        )
                    references.append(
                        TableReference(
                            query_block=block,
                            ref_type="PHYSICAL_TABLE",
                            referenced_query_block=None,
                            table=table,
                            table_alias=alias,
                            source_sql_name=table_name,
                            ordinal_position=ordinal,
                            is_base_source=1 if is_base else 0,
                        )
                    )
                    continue

            if var_table is not None:
                has_variable = False
                for col in iter_columns(select):
                    if col.parent_select is not select:
                        continue
                    if bool(getattr(col, "is_star", False)):
                        continue
                    if not (col.name or "").strip():
                        continue
                    if (col.table or "").strip():
                        continue
                    if col.name.upper() in QueryBlockValidator.DEFAULT_ALLOWED_UNQUALIFIED:
                        has_variable = True
                        break
                if has_variable:
                    ordinal += 1
                    references.append(
                        TableReference(
                            query_block=block,
                            ref_type="VARIABLE",
                            referenced_query_block=None,
                            table=var_table,
                            table_alias=None,
                            source_sql_name=var_table.table_name,
                            ordinal_position=ordinal,
                            is_base_source=0,
                        )
                    )

        return self.dedup_by_key(
            references,
            key_fn=lambda ref: (id(ref.query_block), ref.ordinal_position),
        )
