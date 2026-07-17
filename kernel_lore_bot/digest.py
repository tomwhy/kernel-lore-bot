"""
Turning fetched threads into a ranked digest. Pure; no I/O.

A thread is NEW when its root arrived at or after the cutoff, and UPDATED when
the root is older but the thread saw activity within the window. New threads go
to every subscriber; updated ones only to that thread's followers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from kernel_lore_bot.models import Classified, Thread, ThreadStatus


def count_entries_since(thread: Thread, cutoff: datetime) -> int:
    """Count messages anywhere in the thread that arrived at or after cutoff."""
    return sum(1 for node in thread.walk() if node.entry.updated >= cutoff)


def classify(threads: Iterable[Thread], cutoff: datetime) -> list[Classified]:
    """Pair each thread with its status, new first, then newest first."""
    classified = [
        Classified(
            thread=t,
            status=ThreadStatus.NEW if t.updated >= cutoff else ThreadStatus.UPDATED,
        )
        for t in threads
    ]
    classified.sort(
        key=lambda c: (c.status is not ThreadStatus.NEW, -c.thread.updated.timestamp())
    )
    return classified
