"""
The set of mailing lists lore.kernel.org actually serves.

lore publishes `manifest.js.gz` at its root: a gzipped JSON object keyed by
repository path, e.g. `/linux-media/git/0.git`. The list name is the first
path segment; a list with several epochs appears under several keys, which
collapse into one name.

This exists so a user cannot subscribe to a list that does not exist — a typo
would otherwise become a silent dead subscription that 404s on every scrape.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Iterable

from kernel_lore_bot.http import FetchError, HttpClient
from kernel_lore_bot.sources.lore.mbox import LORE_BASE_URL

log = logging.getLogger(__name__)

MANIFEST_PATH = "/manifest.js.gz"


class ListIndexError(Exception):
    """The manifest was fetched but could not be understood."""


def fetch_list_names(
    client: HttpClient, base_url: str = LORE_BASE_URL
) -> frozenset[str]:
    """Every list name in lore's manifest. Raises FetchError or ListIndexError."""
    raw = client.get(f"{base_url.rstrip('/')}{MANIFEST_PATH}")

    # Whether the body is gzip at all is decided by its magic bytes, not by
    # which exception gzip.decompress happens to raise: a gzip-shaped body
    # with a corrupted CRC32/length trailer (e.g. one flipped bit from a
    # flaky mirror) also raises gzip.BadGzipFile, same as a body with no
    # gzip header. Only the latter is "already decompressed" plaintext.
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise ListIndexError(f"corrupt gzip manifest: {exc}") from exc

    try:
        manifest = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ListIndexError(f"manifest is not JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ListIndexError(f"manifest is a {type(manifest).__name__}, expected object")

    names = {
        key.strip().strip("/").split("/")[0].lower()
        for key in manifest
        if key.strip().strip("/")
    }
    if not names:
        raise ListIndexError("manifest contained no list names")
    return frozenset(names)


@dataclass(frozen=True)
class ListIndex:
    """An immutable snapshot of the valid list names. Names are lowercase."""

    names: frozenset[str]

    def is_valid(self, name: str) -> bool:
        return name.strip().lower() in self.names

    def search(self, query: str, limit: int = 20) -> list[str]:
        """Substring matches, sorted. lore has ~300 lists — browsing is not viable."""
        needle = query.strip().lower()
        if not needle:
            return []
        return sorted(n for n in self.names if needle in n)[:limit]

    def suggest(self, name: str, limit: int = 5) -> list[str]:
        """
        Did-you-mean candidates for an unknown name.

        Substring matches come first — a user typing a partial name is common
        and those matches are the most relevant. Only when that yields nothing
        do we fall back to fuzzy matching, so a one-character typo (e.g.
        "netdevv") still recovers a suggestion instead of a dead end.
        """
        substring_matches = self.search(name, limit=limit)
        if substring_matches:
            return substring_matches

        needle = name.strip().lower()
        if not needle:
            return []
        # sorted() makes the candidate order (and therefore any ties broken
        # by get_close_matches) deterministic across runs.
        return get_close_matches(needle, sorted(self.names), n=limit, cutoff=0.6)


class ListRegistry:
    """
    Holds the current ListIndex and can refresh it in place.

    Starts on `fallback` so the bot is usable before — or without — a
    successful fetch, and keeps the previous index when a refresh fails, so a
    transient lore outage repairs itself on the next scrape rather than
    needing a restart.
    """

    def __init__(
        self,
        client: HttpClient,
        base_url: str = LORE_BASE_URL,
        fallback: Iterable[str] = (),
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._index = ListIndex(frozenset(n.lower() for n in fallback))

    @property
    def index(self) -> ListIndex:
        return self._index

    def refresh(self) -> bool:
        """Fetch a fresh index. Returns False and keeps the old one on failure."""
        try:
            names = fetch_list_names(self._client, self._base_url)
        except (FetchError, ListIndexError) as exc:
            log.error(
                "Could not refresh the lore list index (%s) — keeping %d known list(s)",
                exc,
                len(self._index.names),
            )
            return False
        self._index = ListIndex(names)
        log.info("Lore list index refreshed: %d list(s)", len(names))
        return True
