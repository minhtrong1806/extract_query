from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExtractionContext:
    """Container for shared data across stages (read-only by convention)."""

    ast: Any
    metadata: Any

    catalog_mapping: Optional[Any] = None
    query_blocks: List[Any] = field(default_factory=list)
    node_to_block: Dict[Any, Any] = field(default_factory=dict)
    alias_map: Dict[str, Any] = field(default_factory=dict)
    catalog_tables: List[Any] = field(default_factory=list)
    catalog_columns: List[Any] = field(default_factory=list)
    table_references: List[Any] = field(default_factory=list)
    projections: List[Any] = field(default_factory=list)
    expressions: List[Any] = field(default_factory=list)
    column_lineages: List[Any] = field(default_factory=list)
    column_references: List[Any] = field(default_factory=list)
