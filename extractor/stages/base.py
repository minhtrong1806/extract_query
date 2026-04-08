from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, List, Set, Tuple, TypeVar


T = TypeVar("T")


class BaseStage(ABC):
    def __init__(self, context: Any) -> None:
        self.context = context

    @abstractmethod
    def run(self):
        """Execute stage and return its output."""

    @staticmethod
    def dedup_by_key(items: Iterable[T], key_fn: Callable[[T], Tuple[Any, ...]]) -> List[T]:
        seen: Set[Tuple[Any, ...]] = set()
        result: List[T] = []
        for item in items:
            key = key_fn(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def normalize_sql(sql_text: str) -> str:
        return (sql_text or "").lower()
