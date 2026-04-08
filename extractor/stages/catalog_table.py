from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from tool_gen.core.models import CatalogTable
from tool_gen.core.models import MetadataRecord

from ..constants import VAR_CATALOG_NAME, VAR_SCHEMA_NAME, VAR_TABLE_KEY, VAR_TABLE_NAME
from ..errors import AliasResolutionError
from ..helpers.ast_utils import iter_tables
from ..helpers.resolve_utils import resolve_alias

from .base import BaseStage


class CatalogTableStage(BaseStage):
    def run(self) -> Tuple[List[Any], Dict[str, Any]]:
        """Extract CatalogTable[] and alias_map."""
        ast = self.context.ast
        metadata: MetadataRecord = self.context.metadata

        table_index: Dict[Tuple[str, str | None, str | None], CatalogTable] = {}
        alias_map: Dict[str, CatalogTable] = {}
        alias_scopes: Dict[str, int | None] = {}

        for table in iter_tables(ast):
            if not isinstance(table, exp.Table):
                continue
            key = (table.name, table.db or None, table.catalog or None)
            catalog_table = table_index.get(key)
            if catalog_table is None:
                catalog_table = CatalogTable(
                    table_name=table.name,
                    schema_name=table.db or None,
                    catalog_name=table.catalog or None,
                )
                table_index[key] = catalog_table

            alias = resolve_alias(table)
            if alias:
                alias_key = alias.strip().lower()
                select_node = table.find_ancestor(exp.Select)
                select_id = id(select_node) if select_node is not None else None
                if alias_key in alias_map:
                    if alias_scopes.get(alias_key) == select_id and alias_map[alias_key] is not catalog_table:
                        raise AliasResolutionError(f"Duplicate table alias in same scope: {alias}")
                    continue
                alias_map[alias_key] = catalog_table
                alias_scopes[alias_key] = select_id

        target_table = (metadata.physical_table_name or "").strip()
        if target_table:
            target_key = (target_table, metadata.schema_name, metadata.catalog_name)
            if target_key not in table_index:
                table_index[target_key] = CatalogTable(
                    table_name=target_table,
                    schema_name=metadata.schema_name,
                    catalog_name=metadata.catalog_name,
                )

        if VAR_TABLE_KEY not in table_index:
            table_index[VAR_TABLE_KEY] = CatalogTable(
                table_name=VAR_TABLE_NAME,
                schema_name=VAR_SCHEMA_NAME,
                catalog_name=VAR_CATALOG_NAME,
            )

        return list(table_index.values()), alias_map
