from __future__ import annotations

from typing import Any, Dict, List, Tuple

from sqlglot import expressions as exp

from ast_utils import iter_selects

from ..context import _iter_select_sources, _resolve_alias, build_select_context
from ..formatting import _expression_sql


class CatalogTableStage:
    """Tạo danh sách CatalogTable theo từng QueryBlock."""

    def run(
        self,
        ast: exp.Expression,
        node_to_block: Dict[int, int],
        placeholder_map: Dict[str, str],
        sql_text: str | None,
        global_cte_index: Dict[str, exp.Expression] | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[int, Any]]:
        catalog_tables: List[Dict[str, Any]] = []
        select_context_by_block: Dict[int, Any] = {}
        table_id = 0

        for select in iter_selects(ast):
            block_id = node_to_block.get(id(select))
            if block_id is None:
                continue

            ctx = build_select_context(
                select,
                sql_text=sql_text,
                inherited_cte_index=global_cte_index,
            )
            select_context_by_block[block_id] = ctx

            for source in _iter_select_sources(select):
                if isinstance(source, exp.Subquery):
                    table_id += 1
                    catalog_tables.append(
                        {
                            "table_id": table_id,
                            "block_id": block_id,
                            "table_name": "",
                            "schema_name": "",
                            "catalog_name": "",
                            "alias_name": _resolve_alias(source) or "",
                            "source_type": "SUBQUERY",
                            "raw_sql": _expression_sql(source, placeholder_map, dialect="oracle"),
                        }
                    )
                    continue

                if not isinstance(source, exp.Table):
                    continue
                if not source.name:
                    continue

                source_name = source.name.strip()
                source_key = source_name.lower()
                source_type = "PHYSICAL_TABLE"
                if global_cte_index and source_key in global_cte_index:
                    source_type = "CTE"

                table_id += 1
                catalog_tables.append(
                    {
                        "table_id": table_id,
                        "block_id": block_id,
                        "table_name": source.name or "",
                        "schema_name": source.db or source.catalog or "",
                        "catalog_name": source.catalog or "",
                        "alias_name": _resolve_alias(source) or "",
                        "source_type": source_type,
                        "raw_sql": _expression_sql(source, placeholder_map, dialect="oracle"),
                    }
                )

        return catalog_tables, select_context_by_block
