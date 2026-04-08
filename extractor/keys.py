from __future__ import annotations

from typing import Any, Tuple


def catalog_mapping_key(mapping: Any) -> Tuple[str, str, str]:
    return (mapping.layer_name, mapping.core_object_name, mapping.mapping_name)


def catalog_table_key(table: Any) -> Tuple[str, str, str]:
    return (table.table_name, table.schema_name, table.catalog_name)


def catalog_column_key(column: Any) -> Tuple[Any, str]:
    return (column.table_id, column.column_name)


def query_block_key(query_block: Any) -> Tuple[Any, int]:
    return (
        query_block.mapping_id,
        query_block.ordinal_position,
    )


def table_reference_key(table_ref: Any) -> Tuple[int, Any]:
    return (table_ref.ordinal_position, table_ref.query_block_id)


def projection_key(projection: Any) -> Tuple[int, Any]:
    return (projection.ordinal_position, projection.query_block_id)


def expression_key(expression: Any) -> Tuple[Any, Any, int]:
    return (
        expression.query_block_id,
        expression.parent_expression_id,
        expression.ordinal_position,
    )


def column_lineage_key(lineage: Any) -> Tuple[Any, Any]:
    return (lineage.src_column_ref_id, lineage.projection_id)


def column_reference_key(column_ref: Any) -> Tuple[Any, str, Any, int]:
    return (
        column_ref.query_block_id,
        column_ref.clause_type,
        column_ref.expression_id,
        column_ref.ordinal_position,
    )
