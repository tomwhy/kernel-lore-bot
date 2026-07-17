"""
Thread filters. Pure; no I/O.

To add a filter, write a class with an `allows` method and pass it to
apply_filters. Nothing else needs to change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from kernel_lore_bot.models import Thread

log = logging.getLogger(__name__)


class Filter(Protocol):
    def allows(self, thread: Thread) -> bool:
        """Return False to drop the thread."""
        ...


@dataclass(frozen=True)
class BlockedAuthors:
    """Drops threads whose author matches any name, case-insensitive substring."""

    names: tuple[str, ...]

    def allows(self, thread: Thread) -> bool:
        author = thread.author.lower()
        for blocked in self.names:
            if blocked.lower() in author:
                log.debug("Blocked by author filter: %r (%s)", thread.title, thread.author)
                return False
        return True


def apply_filters(threads: Iterable[Thread], filters: Sequence[Filter]) -> list[Thread]:
    """Keep only threads that every filter allows."""
    return [t for t in threads if all(f.allows(t) for f in filters)]
