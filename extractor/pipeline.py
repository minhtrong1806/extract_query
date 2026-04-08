from __future__ import annotations

from typing import Any, Dict

from .context import ExtractionContext
from .stages.catalog_mapping import CatalogMappingStage
from .stages.query_block import QueryBlockStage
from .stages.catalog_table import CatalogTableStage
from .stages.catalog_column import CatalogColumnStage
from .stages.table_reference import TableReferenceStage
from .stages.projection import ProjectionStage
from .stages.expression import ExpressionStage
from .stages.column_lineage import ColumnLineageStage
from .stages.column_reference import ColumnReferenceStage


def run_pipeline(ast: Any, metadata: Any) -> Dict[str, Any]:
    """Run extraction stages in the standard flow order.

    Returns a dict of all extracted entities for upsert layer.
    """

    context = ExtractionContext(ast=ast, metadata=metadata)

    context.catalog_mapping = CatalogMappingStage(context).run()

    query_blocks, node_to_block = QueryBlockStage(context).run()
    context.query_blocks = query_blocks
    context.node_to_block = node_to_block

    catalog_tables, alias_map = CatalogTableStage(context).run()
    context.catalog_tables = catalog_tables
    context.alias_map = alias_map

    context.catalog_columns = CatalogColumnStage(context).run()
    context.table_references = TableReferenceStage(context).run()
    context.projections = ProjectionStage(context).run()
    context.expressions = ExpressionStage(context).run()
    context.column_lineages = ColumnLineageStage(context).run()
    context.column_references = ColumnReferenceStage(context).run()

    return {
        "catalog_mapping": context.catalog_mapping,
        "catalog_tables": context.catalog_tables,
        "catalog_columns": context.catalog_columns,
        "query_blocks": context.query_blocks,
        "table_references": context.table_references,
        "projections": context.projections,
        "expressions": context.expressions,
        "column_lineages": context.column_lineages,
        "column_references": context.column_references,
    }
