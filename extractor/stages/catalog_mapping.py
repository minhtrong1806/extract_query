from __future__ import annotations

from typing import Any

from tool_gen.core.models import CatalogMapping
from tool_gen.core.models import MetadataRecord

from .base import BaseStage


class CatalogMappingStage(BaseStage):
    def run(self) -> Any:
        """Map metadata -> CatalogMapping."""
        metadata: MetadataRecord = self.context.metadata

        mapping_name = metadata.physical_table_name
        return CatalogMapping(
            mapping_name=mapping_name,
            layer_name=metadata.layer,
            core_object_name=metadata.core_object,
            catalog_name=metadata.catalog_name,
            schema_name=metadata.schema_name,
            domain_name=getattr(metadata, "domain", None),
            source_file_path=str(metadata.mapping_file_path) if metadata.mapping_file_path else None,
            source_sheet_name=metadata.sheet_name,
        )
