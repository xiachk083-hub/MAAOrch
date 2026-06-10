from __future__ import annotations
from datetime import datetime
from dataclasses import dataclass, field


@dataclass(order=True, frozen=True)
class QueueEntry:
    """A launch request waiting in the queue."""
    sort_key: tuple  # (priority, not_before) — used by heapq
    account_id: str = field(compare=False)
    source: str = field(compare=False)        # "manual" | "schedule" | "sanity"
    not_before: datetime = field(compare=False)

    @staticmethod
    def make(account_id: str, source: str, priority: int = 0,
             not_before: datetime | None = None) -> "QueueEntry":
        nb = not_before or datetime.now()
        return QueueEntry(sort_key=(priority, nb), account_id=account_id,
                          source=source, not_before=nb)
