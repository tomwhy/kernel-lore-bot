"""
The disk boundary.

State is subscriber-centric: `{chat_id: Subscriber}`. A chat present as a key
is subscribed, even with no follows. A reverse index (thread -> chats) is
maintained in memory so the broadcast hot path does not scan subscribers.

`Subscriber` is the domain model and knows nothing about how it is stored;
each backend owns its own serialization (see json_store for the JSON mapping).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable, Protocol

log = logging.getLogger(__name__)


@dataclass
class Subscriber:
    """One subscribed chat: the threads it follows, the lists it wants, and
    the authors it has muted."""

    chat_id: int
    follows: set[str] = field(default_factory=set)
    mailing_lists: set[str] = field(default_factory=set)
    blocked_authors: set[str] = field(default_factory=set)


class Store(Protocol):
    def subscribers(self) -> set[int]: ...
    def add_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscribers(self, chat_ids: Iterable[int]) -> None: ...
    def follow(self, thread_id: str, chat_id: int) -> bool: ...
    def unfollow(self, thread_id: str, chat_id: int) -> bool: ...
    def followers(self, thread_id: str) -> list[int]: ...
    def following_count(self, chat_id: int) -> int: ...
    def mailing_lists(self, chat_id: int) -> set[str]: ...
    def add_lists(self, chat_id: int, names: Iterable[str]) -> set[str]: ...
    def remove_lists(self, chat_id: int, names: Iterable[str]) -> set[str]: ...
    def blocked_authors(self, chat_id: int) -> set[str]: ...
    def block(self, chat_id: int, name: str) -> bool: ...
    def unblock(self, chat_id: int, name: str) -> bool: ...
    def all_mailing_lists(self) -> set[str]: ...


class BaseStore:
    """In-memory implementation of Store. Subclasses add persistence via _flush."""

    def __init__(
        self,
        subs: dict[int, Subscriber] | None = None,
        default_lists: Iterable[str] = (),
        default_blocks: Iterable[str] = (),
    ) -> None:
        # Copy each Subscriber and every mutable set it owns so a caller's
        # objects are not aliased into our state. `replace` rather than
        # Subscriber(...) so fields added later are carried over for free.
        self._subs: dict[int, Subscriber] = {
            sub.chat_id: replace(
                sub,
                follows=set(sub.follows),
                mailing_lists=set(sub.mailing_lists),
                blocked_authors=set(sub.blocked_authors),
            )
            for sub in (subs or {}).values()
        }
        self._default_lists = frozenset(default_lists)
        self._default_blocks = frozenset(default_blocks)
        self._index: dict[str, set[int]] = defaultdict(set)
        for chat, sub in self._subs.items():
            for thread_id in sub.follows:
                self._index[thread_id].add(chat)

    # -- persistence hook ---------------------------------------------

    def _flush(self) -> None:
        """Called after every mutation. No-op in memory."""

    # -- reads ---------------------------------------------------------

    def subscribers(self) -> set[int]:
        return set(self._subs)

    def followers(self, thread_id: str) -> list[int]:
        return list(self._index.get(thread_id, ()))

    def following_count(self, chat_id: int) -> int:
        sub = self._subs.get(chat_id)
        return len(sub.follows) if sub else 0

    def mailing_lists(self, chat_id: int) -> set[str]:
        sub = self._subs.get(chat_id)
        return set(sub.mailing_lists) if sub else set()

    def blocked_authors(self, chat_id: int) -> set[str]:
        sub = self._subs.get(chat_id)
        return set(sub.blocked_authors) if sub else set()

    def all_mailing_lists(self) -> set[str]:
        """Every list at least one subscriber wants — the scrape's scope.

        Scans subscribers rather than keeping an index: this runs once per
        scrape, not once per delivered message, so it is not a hot path.
        """
        union: set[str] = set()
        for sub in self._subs.values():
            union |= sub.mailing_lists
        return union

    # -- writes --------------------------------------------------------

    def add_subscriber(self, chat_id: int) -> bool:
        if chat_id in self._subs:
            return False
        self._subs[chat_id] = Subscriber(
            chat_id,
            mailing_lists=set(self._default_lists),
            blocked_authors=set(self._default_blocks),
        )
        self._flush()
        log.info("New subscriber: chat_id=%d (total: %d)", chat_id, len(self._subs))
        return True

    def remove_subscriber(self, chat_id: int) -> bool:
        sub = self._subs.pop(chat_id, None)
        if sub is None:
            return False
        for thread_id in sub.follows:
            followers = self._index.get(thread_id)
            if followers:
                followers.discard(chat_id)
                if not followers:
                    del self._index[thread_id]
        self._flush()
        log.info("Unsubscribed: chat_id=%d (total: %d)", chat_id, len(self._subs))
        return True

    def remove_subscribers(self, chat_ids: Iterable[int]) -> None:
        changed = False
        for chat_id in chat_ids:
            sub = self._subs.pop(chat_id, None)
            if sub is None:
                continue
            changed = True
            for thread_id in sub.follows:
                followers = self._index.get(thread_id)
                if followers:
                    followers.discard(chat_id)
                    if not followers:
                        del self._index[thread_id]
        if changed:
            self._flush()

    def follow(self, thread_id: str, chat_id: int) -> bool:
        if chat_id not in self._subs:
            self._subs[chat_id] = Subscriber(
                chat_id,
                mailing_lists=set(self._default_lists),
                blocked_authors=set(self._default_blocks),
            )
        sub = self._subs[chat_id]
        if thread_id in sub.follows:
            return False
        sub.follows.add(thread_id)
        self._index[thread_id].add(chat_id)
        self._flush()
        log.info("chat_id=%d now following thread %s", chat_id, thread_id)
        return True

    def unfollow(self, thread_id: str, chat_id: int) -> bool:
        sub = self._subs.get(chat_id)
        if sub is None or thread_id not in sub.follows:
            return False
        sub.follows.discard(thread_id)
        followers = self._index.get(thread_id)
        if followers:
            followers.discard(chat_id)
            if not followers:
                del self._index[thread_id]
        self._flush()
        log.info("chat_id=%d unfollowed thread %s", chat_id, thread_id)
        return True

    def add_lists(self, chat_id: int, names: Iterable[str]) -> set[str]:
        sub = self._subs.get(chat_id)
        if sub is None:
            return set()
        added = {name for name in names if name not in sub.mailing_lists}
        if not added:
            return set()
        sub.mailing_lists |= added
        self._flush()
        log.info("chat_id=%d added list(s): %s", chat_id, ", ".join(sorted(added)))
        return added

    def remove_lists(self, chat_id: int, names: Iterable[str]) -> set[str]:
        sub = self._subs.get(chat_id)
        if sub is None:
            return set()
        removed = {name for name in names if name in sub.mailing_lists}
        if not removed:
            return set()
        sub.mailing_lists -= removed
        self._flush()
        log.info("chat_id=%d removed list(s): %s", chat_id, ", ".join(sorted(removed)))
        return removed

    def block(self, chat_id: int, name: str) -> bool:
        sub = self._subs.get(chat_id)
        if sub is None:
            return False
        # Blocks match case-insensitively (see filters.BlockedAuthors), so two
        # spellings of one name would be a duplicate rule, not two rules.
        if any(existing.lower() == name.lower() for existing in sub.blocked_authors):
            return False
        sub.blocked_authors.add(name)
        self._flush()
        log.info("chat_id=%d blocked author %r", chat_id, name)
        return True

    def unblock(self, chat_id: int, name: str) -> bool:
        sub = self._subs.get(chat_id)
        if sub is None:
            return False
        match = next(
            (e for e in sub.blocked_authors if e.lower() == name.lower()), None
        )
        if match is None:
            return False
        sub.blocked_authors.discard(match)
        self._flush()
        log.info("chat_id=%d unblocked author %r", chat_id, match)
        return True
