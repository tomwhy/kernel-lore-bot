"""Pure parsing of lore's per-list `new.atom` feed. This module performs no I/O."""

from __future__ import annotations

import logging
import pathlib
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"


class FeedParseError(Exception):
    """The feed page was not parseable XML."""


@dataclass(frozen=True)
class FeedEntry:
    """One `<entry>` in a feed page: just what pagination needs."""

    entry_id: str  # Message-ID taken from the link href
    updated: datetime


def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def parse_feed_page(data: bytes) -> list[FeedEntry]:
    """
    Parse one feed page into FeedEntry objects, in document order.

    Individual entries missing a usable date or link are skipped: one bad entry
    must not abort a whole list. Malformed XML raises FeedParseError, which the
    caller treats as the end of that list.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedParseError(f"Malformed feed XML: {exc}") from exc

    entries: list[FeedEntry] = []

    entry_els = root.findall(_tag(ATOM_NS, "entry"))
    for entry_el in entry_els:
        updated_raw = (entry_el.findtext(_tag(ATOM_NS, "updated")) or "").strip()
        try:
            updated = datetime.fromisoformat(updated_raw)
        except ValueError:
            log.debug("Skipping feed entry with bad <updated>: %r", updated_raw)
            continue

        link_el = entry_el.find(_tag(ATOM_NS, "link"))
        href = link_el.get("href", "") if link_el is not None else ""
        entry_id = pathlib.Path(urllib.parse.urlparse(href).path).name
        if not entry_id:
            log.debug("Skipping feed entry with no usable link href: %r", href)
            continue

        entries.append(FeedEntry(entry_id=entry_id, updated=updated))

    if entry_els and not entries:
        # Every <entry> on this page was skipped. A single bad entry is
        # expected and harmless (logged at debug above), but a page-wide
        # wipeout is indistinguishable from legitimate end-of-pagination to
        # the caller (both return []) — if lore ever changed its date format
        # feed-wide, the bot would go permanently, silently quiet. Surface
        # that distinction loudly here.
        log.warning(
            "All %d entr(ies) on this feed page were unparseable — "
            "returning no entries (this may look like end-of-pagination)",
            len(entry_els),
        )

    return entries
