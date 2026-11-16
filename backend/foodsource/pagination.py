from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import Query


@dataclass(frozen=True)
class Page[T]:
    items: Sequence[T]
    total: int
    next_offset: int | None


@dataclass(frozen=True)
class PageRequest:
    limit: Annotated[int, Query(ge=1, le=100)] = 30
    offset: Annotated[int, Query(ge=0)] = 0

    def select[T](self, records: Sequence[T]) -> Page[T]:
        end = self.offset + self.limit
        total = len(records)
        return Page(records[self.offset : end], total, end if end < total else None)
