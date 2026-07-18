"""The Source protocol. One implementation today: LoreSource."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol, Sequence

from kernel_lore_bot.models import Thread


class Source(Protocol):
    def fetch_threads(
        self, since: datetime, mailing_lists: Sequence[str]
    ) -> Iterable[Thread]:
        """Every thread with activity at or after `since`, across `mailing_lists`."""
        ...

    def fetch_threads_by_id(self, ids: Iterable[str]) -> Iterable[Thread]:
        """Fetch each given Message-ID as its own thread, unconditionally --
        no `since` filter, unlike fetch_threads. A failed fetch is skipped,
        not raised."""
        ...
