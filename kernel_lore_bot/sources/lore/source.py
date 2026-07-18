"""
LoreSource: the only Source implementation.

Walks each given mailing list's `new.atom` backwards in time, and for every
entry newer than the cutoff downloads that thread's full mbox and parses it
into a Thread. Threads are deduplicated across lists by message-id, since one
thread is frequently posted to several lists, and a thread found on more than
one list has its mailing list names unioned rather than the later fetch
overwriting the earlier one.
"""

from __future__ import annotations

import gzip
import logging
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, Optional, Sequence

from kernel_lore_bot.http import FetchError, HttpClient
from kernel_lore_bot.models import Thread
from kernel_lore_bot.progress import NullProgress, Progress
from kernel_lore_bot.sources.lore import mbox as mbox_parser
from kernel_lore_bot.sources.lore.atom import FeedEntry, FeedParseError, parse_feed_page

log = logging.getLogger(__name__)

# Generous upper bound on pages fetched per mailing list in a single run. Lore's
# atom pages carry ~50 entries each, so this caps a single list's backfill at
# roughly 500k entries -- far beyond any real mailing list's total thread count
# -- while still guaranteeing termination against a server that keeps advancing
# `t` forever (e.g. a page-size-1 feed that never repeats).
MAX_FEED_PAGES_PER_LIST = 10_000


class LoreSource:
    """Fetches threads from lore.kernel.org."""

    def __init__(
        self,
        client: HttpClient,
        progress: Progress | None = None,
        base_url: str = mbox_parser.LORE_BASE_URL,
    ) -> None:
        self.client = client
        self.progress = progress if progress is not None else NullProgress()
        self.base_url = base_url.rstrip("/")

    # -- public API ---------------------------------------------------

    def fetch_threads(
        self, since: datetime, mailing_lists: Sequence[str]
    ) -> list[Thread]:
        """
        Every thread with activity at or after `since`, deduplicated.

        A thread cross-posted to several lists is downloaded once and carries
        all of their names. That means results cannot be yielded as they are
        found — a later list may add to a thread already seen — so this
        collects fully before returning.
        """
        # node Message-ID -> root Message-ID of the thread that contains it.
        # Keyed on every node, not just roots, so a reply appearing in a feed
        # resolves to its thread instead of triggering a second download.
        seen: dict[str, str] = {}
        threads: dict[str, Thread] = {}

        for list_name in mailing_lists:
            with self.progress.bar(f"  {list_name}") as bar:
                for feed_entry in self._iter_feed_entries(list_name, since):
                    bar.update(1)

                    root_id = seen.get(feed_entry.entry_id)
                    if root_id is not None:
                        existing = threads.get(root_id)
                        if existing is not None:
                            threads[root_id] = replace(
                                existing,
                                mailing_lists=existing.mailing_lists | {list_name},
                            )
                        continue

                    thread = self._fetch_thread(feed_entry.entry_id, list_name)
                    if thread is None:
                        # Remember the failure so the next list does not retry it.
                        seen[feed_entry.entry_id] = ""
                        continue

                    existing = threads.get(thread.id)
                    if existing is not None:
                        thread = replace(
                            thread, mailing_lists=existing.mailing_lists | thread.mailing_lists
                        )
                    threads[thread.id] = thread
                    for node in thread.walk():
                        seen[node.entry.id] = thread.id

        return list(threads.values())

    def fetch_threads_by_id(self, ids: Iterable[str]) -> list[Thread]:
        """
        Fetch each given Message-ID as its own thread, by id rather than by
        feed. No `since` filter -- every id is downloaded unconditionally.

        Used for a subscriber's followed threads, which may lie outside
        every mailing list any subscriber currently wants (see
        Broadcaster.collect). `list_name=""` means the resulting Thread's
        mailing_lists is always frozenset() -- a thread reached this way has
        no known list, and does not need one: followers bypass visible_for
        entirely. A fetch that fails is skipped (already logged by
        _fetch_thread) rather than aborting the rest.
        """
        threads = []
        for entry_id in ids:
            thread = self._fetch_thread(entry_id, "")
            if thread is not None:
                threads.append(thread)
        return threads

    # -- internals ----------------------------------------------------

    def _iter_feed_entries(self, list_name: str, since: datetime) -> Iterator[FeedEntry]:
        """
        Page through `<base>/<list>/new.atom`, newest first, until an entry older
        than `since` appears or a page comes back empty.
        """
        url = f"{self.base_url}/{list_name}/new.atom"
        timestamp = datetime.now(timezone.utc)

        for _ in range(MAX_FEED_PAGES_PER_LIST):
            try:
                data = self.client.get(url, params={"t": timestamp.strftime("%Y%m%d%H%M%S")})
                entries = parse_feed_page(data)
            except (FetchError, FeedParseError) as exc:
                log.warning("Skipping list %s at t=%s: %s", list_name, timestamp, exc)
                return

            if not entries:
                return

            for entry in entries:
                if entry.updated < since:
                    return
                yield entry

            # Next page starts just before the oldest entry we just saw.
            next_timestamp = entries[-1].updated - timedelta(seconds=1)
            if next_timestamp >= timestamp:
                # The server didn't honor `t` (or the feed is misbehaving) and
                # kept handing back a page that doesn't move us backwards in
                # time. Without this guard we'd request the same page forever.
                log.warning(
                    "List %s stopped paginating: page at t=%s did not advance "
                    "(next would be t=%s); server may be ignoring the t param",
                    list_name,
                    timestamp,
                    next_timestamp,
                )
                return
            timestamp = next_timestamp
        else:
            log.warning(
                "List %s hit the %d-page cap without exhausting the feed; stopping",
                list_name,
                MAX_FEED_PAGES_PER_LIST,
            )

    def _fetch_thread(self, entry_id: str, list_name: str) -> Optional[Thread]:
        url = f"{self.base_url}/all/{entry_id}/t.mbox.gz"
        try:
            raw = self.client.get(url)
        except FetchError as exc:
            log.warning("Could not fetch mbox %s: %s", url, exc)
            return None

        try:
            raw = gzip.decompress(raw)
        except gzip.BadGzipFile:
            pass  # server sent an uncompressed mbox; use the bytes as-is
        except (EOFError, zlib.error) as exc:
            # Any other way a gzip body can be broken: EOFError for a truncated
            # stream (connection cut mid-download, neither BadGzipFile nor
            # OSError so it must be caught by name), zlib.error for corruption
            # mid-stream (e.g. a flipped bit) that isn't a header/CRC problem.
            # This except clause runs only when the body IS gzip-shaped but
            # broken -- the BadGzipFile clause above already claimed the
            # "not gzip at all" case, so that plaintext fallback is untouched.
            log.warning("Corrupted/truncated gzip mbox at %s: %s", url, exc)
            return None

        thread = mbox_parser.parse_thread(
            raw.decode("utf-8", errors="replace"), list_name, base_url=self.base_url
        )
        if thread is None:
            log.debug("No usable messages in mbox at %s", url)
        return thread
