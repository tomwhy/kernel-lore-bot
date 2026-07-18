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
    """One subscribed chat and the threads it follows."""

    chat_id: int
    follows: set[str] = field(default_factory=set)


class Store(Protocol):
    def subscribers(self) -> set[int]: ...
    def add_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscribers(self, chat_ids: Iterable[int]) -> None: ...
    def follow(self, thread_id: str, chat_id: int) -> bool: ...
    def unfollow(self, thread_id: str, chat_id: int) -> bool: ...
    def followers(self, thread_id: str) -> list[int]: ...
    def following_count(self, chat_id: int) -> int: ...


class BaseStore:
    """In-memory implementation of Store. Subclasses add persistence via _flush."""

    def __init__(self, subs: dict[int, Subscriber] | None = None) -> None:
        # Copy each Subscriber (and its mutable follows set) so a caller's
        # objects are not aliased into our state. `replace` rather than
        # Subscriber(...) so fields added later are carried over for free.
        self._subs: dict[int, Subscriber] = {
            sub.chat_id: replace(sub, follows=set(sub.follows))
            for sub in (subs or {}).values()
        }
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

    # -- writes --------------------------------------------------------

    def add_subscriber(self, chat_id: int) -> bool:
        if chat_id in self._subs:
            return False
        self._subs[chat_id] = Subscriber(chat_id)
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
        sub = self._subs.setdefault(chat_id, Subscriber(chat_id))
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
