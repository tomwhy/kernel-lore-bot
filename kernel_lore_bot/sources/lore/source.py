"""
LoreSource: the only Source implementation.

Walks each configured list's `new.atom` backwards in time, and for every entry
newer than the cutoff downloads that thread's full mbox and parses it into a
Thread. Threads are deduplicated across lists by message-id, since one thread is
frequently posted to several lists.
"""

from __future__ import annotations

import gzip
import logging
import zlib
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

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
        mailing_lists: tuple[str, ...],
        progress: Progress | None = None,
        base_url: str = mbox_parser.LORE_BASE_URL,
    ) -> None:
        self.client = client
        self.mailing_lists = tuple(mailing_lists)
        self.progress = progress if progress is not None else NullProgress()
        self.base_url = base_url.rstrip("/")

    # -- public API ---------------------------------------------------

    def fetch_threads(self, since: datetime) -> Iterator[Thread]:
        """Yield every thread with activity at or after `since`, deduplicated."""
        seen: set[str] = set()

        for list_name in self.mailing_lists:
            with self.progress.bar(f"  {list_name}") as bar:
                for feed_entry in self._iter_feed_entries(list_name, since):
                    bar.update(1)

                    if feed_entry.entry_id in seen:
                        continue

                    thread = self._fetch_thread(feed_entry.entry_id, list_name)
                    if thread is None:
                        seen.add(feed_entry.entry_id)
                        continue

                    seen.update(node.entry.id for node in thread.walk())
                    yield thread

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
