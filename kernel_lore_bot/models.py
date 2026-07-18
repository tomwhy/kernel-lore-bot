"""Pure data structures. This module imports no I/O."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional


@dataclass(frozen=True)
class Reply:
    """The In-Reply-To reference on a non-root entry."""

    ref: str  # Message-ID of the parent, without angle brackets


@dataclass(frozen=True, eq=False)
class Entry:
    """A single email message parsed from an mbox."""

    id: str  # Message-ID, without angle brackets
    title: str
    url: str
    author: str
    updated: datetime
    reply: Optional[Reply]  # None <-> this is a thread root

    @property
    def is_reply(self) -> bool:
        return self.reply is not None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entry) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class Node:
    """One message in the thread tree, with its direct replies as children."""

    entry: Entry
    children: tuple[Node, ...] = ()

    def walk(self) -> Iterator[Node]:
        """Yield this node, then every descendant (depth-first)."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Thread:
    """
    An email thread reconstructed from its mbox archive.

    `roots` is normally exactly one node; more than one signals a split or
    malformed thread, which is kept rather than dropped.

    `mailing_lists` holds every list the thread was seen on. One thread is
    frequently cross-posted, and subscribers pick lists individually, so the
    full set — not just whichever list surfaced it first — decides who
    receives it.
    """

    roots: tuple[Node, ...]
    mailing_lists: frozenset[str] = frozenset()

    def walk(self) -> Iterator[Node]:
        for root in self.roots:
            yield from root.walk()

    @property
    def title(self) -> str:
        return self.roots[0].entry.title

    @property
    def author(self) -> str:
        return self.roots[0].entry.author

    @property
    def updated(self) -> datetime:
        return self.roots[0].entry.updated

    @property
    def url(self) -> str:
        return self.roots[0].entry.url

    @property
    def id(self) -> str:
        return self.roots[0].entry.id


class ThreadStatus(enum.Enum):
    NEW = "new"
    UPDATED = "updated"


@dataclass(frozen=True)
class Classified:
    """A thread paired with the status it was given for this run."""

    thread: Thread
    status: ThreadStatus
