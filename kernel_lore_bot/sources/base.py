"""The Source protocol. One implementation today: LoreSource."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from kernel_lore_bot.models import Thread


class Source(Protocol):
    def fetch_threads(self, since: datetime) -> Iterable[Thread]:
        """Yield every thread with activity at or after `since`."""
        ...
