"""
Thread filters. Pure; no I/O.

A filter is any class with an `allows` method. apply_filters() composes a
sequence of them, but it has no production callers today: the real
extension point is Broadcaster.visible_for (kernel_lore_bot/delivery/
broadcast.py), which hardcodes BlockedAuthors. To add a filter that actually
runs, wire it into visible_for -- writing an `allows` class alone runs it
nowhere.
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


def normalize_address(value: str) -> str:
    """The single on-disk spelling of a mailbox: stripped and lowercased."""
    return value.strip().lower()


def looks_like_address(value: str) -> bool:
    """
    Whether `value` is shaped like an email address rather than a name.

    Deliberately loose — this is not RFC 5322 validation, and it is not
    trying to prove the mailbox exists. It only has to separate the two
    kinds of thing that end up in a blocklist: `Kernel Test Robot` (a
    display name, which can never match now) from `lkp@intel.com`. Local
    part and domain must both be non-empty, there must be exactly one "@",
    and no whitespace anywhere.
    """
    address = value.strip()
    if not address or any(ch.isspace() for ch in address):
        return False
    local, sep, domain = address.partition("@")
    return bool(sep) and bool(local) and bool(domain) and "@" not in domain


@dataclass(frozen=True)
class BlockedAuthors:
    """
    Drops threads whose author's address is exactly one of `emails`.

    The match is on the address only — never the display name, which is
    neither unique nor stable — and is exact rather than a substring, so
    blocking `lkp@intel.com` cannot also mute `not-lkp@intel.com.evil.org`.
    Both sides are lowercased and stripped first; that is normalisation of
    one address, not a loosening of the match.

    A thread whose From: carried no parseable address has author_email ==
    "". It is always allowed: an empty stored entry is a data-entry slip,
    and letting it match every addressless thread would silently mute mail
    nobody chose to block.
    """

    emails: tuple[str, ...]

    def allows(self, thread: Thread) -> bool:
        author_email = thread.author_email.strip().lower()
        if not author_email:
            return True
        for blocked in self.emails:
            if blocked.strip().lower() == author_email:
                log.debug(
                    "Blocked by author filter: %r (%s)", thread.title, author_email
                )
                return False
        return True


def apply_filters(threads: Iterable[Thread], filters: Sequence[Filter]) -> list[Thread]:
    """Keep only threads that every filter allows."""
    return [t for t in threads if all(f.allows(t) for f in filters)]
