from __future__ import annotations


class ExtractorError(Exception):
    """Base error for extractor."""


class AliasResolutionError(ExtractorError):
    pass


class AmbiguousColumnError(ExtractorError):
    pass


class MissingTableError(ExtractorError):
    pass


class MissingColumnError(ExtractorError):
    pass


class PersistenceError(Exception):
    """Base error for persist/upsert layer."""


class MissingForeignKeyError(PersistenceError):
    pass


class MissingKeyError(PersistenceError):
    pass
