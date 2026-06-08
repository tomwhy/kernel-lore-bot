"""
scraper.py – fetch and classify kernel lore Atom feeds
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Generator

import requests

import config

log = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
THREAD_NS = "http://purl.org/syndication/thread/1.0"
KERNEL_LORE_URL = "https://lore.kernel.org/all/new.atom"


# ------------------------------------------------------------------ #
#  Data classes                                                        #
# ------------------------------------------------------------------ #

@dataclass
class Reply:
    """The thr:in-reply-to reference on a non-root entry."""
    ref: str            # message-ID (the `ref` attribute)
    href: str           # URL of the parent entry


@dataclass
class Entry:
    """A single email message parsed from an Atom entry."""
    id: str
    title: str
    url: str
    author: str
    updated: datetime
    reply: Optional[Reply]          # None ↔ this is a thread root

    @property
    def is_reply(self) -> bool:
        return self.reply is not None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entry) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class Node:
    """One message in the thread tree, with its direct replies as children."""
    entry: Entry
    children: list[Node] = field(default_factory=list)


@dataclass
class Thread:
    """
    A single email thread fetched from /t.atom.

    `roots`  – one or more root nodes (entries with no thr:in-reply-to).
               Normally exactly one; >1 signals a malformed/split thread.
    `status` – 'new' | 'updated'

    Convenience properties delegate to roots[0] for sorting/header use.
    """
    roots: list[Node]
    status: str = ""            # 'new' | 'updated'

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


# ------------------------------------------------------------------ #
#  XML helpers                                                         #
# ------------------------------------------------------------------ #

def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def _parse_entry(entry_el: ET.Element) -> Optional[Entry]:
    """Parse one <entry> element into an Entry dataclass. Returns None on error."""
    try:
        updated_raw = (entry_el.findtext(_tag(ATOM_NS, "updated")) or "").strip()
        updated = datetime.fromisoformat(updated_raw)

        eid   = (entry_el.findtext(_tag(ATOM_NS, "id"))    or "").strip()
        title = (entry_el.findtext(_tag(ATOM_NS, "title")) or "").strip()

        link_el    = entry_el.find(_tag(ATOM_NS, "link"))
        thread_url = link_el.get("href", "") if link_el is not None else ""

        # Strip trailing slash so we can append /t.atom cleanly later
        thread_url = thread_url.rstrip("/")

        author_el = entry_el.find(_tag(ATOM_NS, "author"))
        author    = (
            (author_el.findtext(_tag(ATOM_NS, "name")) or "").strip()
            if author_el is not None
            else "Unknown"
        )

        # thr:in-reply-to → present only on non-root entries
        irt_el = entry_el.find(_tag(THREAD_NS, "in-reply-to"))
        if irt_el is not None:
            reply = Reply(
                ref  = irt_el.get("ref",  ""),
                href = irt_el.get("href", "").rstrip("/"),
            )
        else:
            reply = None

        return Entry(
            id=eid,
            title=title,
            url=thread_url,
            author=author,
            updated=updated,
            reply=reply,
        )
    except Exception as exc:          # noqa: BLE001
        log.debug("Skipping malformed entry: %s", exc)
        return None


# ------------------------------------------------------------------ #
#  Title parsing and blacklist filtering                               #
# ------------------------------------------------------------------ #

# Matches a single bracket tag: [PATCH], [RFC v2], [RESEND PATCH net], etc.
_TAG_RE = re.compile(r"\[([^\]]*)\]")


@dataclass
class ParsedTitle:
    tags:     list[str]     # content inside each [...] bracket, stripped
    subjects: list[str]     # colon-delimited subjects after the tags
    rest:     str           # the remaining description


def parse_title(title: str) -> ParsedTitle:
    """
    Split a kernel mailing list subject line into tags, subjects, and rest.

    Examples:
      "[PATCH v2] net: sched: fix skb leak"
        → tags=["PATCH v2"], subjects=["net", "sched"], rest="fix skb leak"

      "[RFC] add new syscall"
        → tags=["RFC"], subjects=[], rest="add new syscall"

      "Re: discussion about mm"
        → tags=[], subjects=[], rest="Re: discussion about mm"
    """
    
    reply = False
    if title.lower().startswith("re:"):
        title = title[len("re:"):].strip()
        reply=True

    # Extract all [...] tags from the front of the title
    tags: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(title):
        # Only consume tags that appear before any non-bracket, non-space text
        if m.start() != pos and title[pos:m.start()].strip():
            break
        tags.append(m.group(1).strip())
        pos = m.end()

    remainder = title[pos:].strip()

    # Split off colon-delimited subjects; stop when a segment contains spaces
    # (that signals we've hit the free-text description)
    subjects: list[str] = []
    while ":" in remainder:
        head, _, tail = remainder.partition(":")
        head = head.strip()
        if " " in head:
            break                   # e.g. "fix skb leak in net" — not a subject
        subjects.append(head)
        remainder = tail.strip()

    return ParsedTitle(tags=tags, subjects=subjects, rest=remainder)


def _is_blacklisted(title: str) -> bool:
    """
    Return True if the parsed title matches any blacklist.

    - BLACKLIST_TAGS:     exact match (case-insensitive) against each tag
    - BLACKLIST_SUBJECTS: exact match (case-insensitive) against each subject
    - BLACKLIST_TITLE:    substring match (case-insensitive) against the rest
    """
    parsed = parse_title(title)

    tags_lower     = {t.lower() for t in parsed.tags}
    subjects_lower = {s.lower() for s in parsed.subjects}
    rest_lower     = parsed.rest.lower()

    if any(kw.lower() in tags_lower     for kw in config.BLACKLIST_TAGS):
        return True
    if any(kw.lower() in subjects_lower for kw in config.BLACKLIST_SUBJECTS):
        return True
    if any(kw.lower() in rest_lower     for kw in config.BLACKLIST_TITLE):
        return True

    return False


# ------------------------------------------------------------------ #
#  HTTP helpers                                                        #
# ------------------------------------------------------------------ #

def _fetch_xml(url: str) -> Optional[ET.Element]:
    """Fetch a URL and return the parsed XML root, or None on error."""
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": "kernel-lore-bot/1.0"},
        )
        resp.raise_for_status()
        return ET.fromstring(resp.content)
    except requests.RequestException as exc:
        log.warning("HTTP error fetching %s: %s", url, exc)
    except ET.ParseError as exc:
        log.warning("XML parse error for %s: %s", url, exc)
    return None


# ------------------------------------------------------------------ #
#  new.atom pagination                                                 #
# ------------------------------------------------------------------ #

def _fetch_new_entries(after: datetime) -> Generator[Entry, None, None]:
    """
    Paginate lore.kernel.org/all/new.atom backwards in time,
    yielding Entry objects whose updated >= after.
    Stops as soon as an entry older than `after` is encountered.
    """
    timestamp = datetime.now(timezone.utc)

    while True:
        try:
            resp = requests.get(
                KERNEL_LORE_URL,
                params={"t": timestamp.strftime("%Y%m%d%H%M%S")},
                timeout=config.REQUEST_TIMEOUT,
                headers={"User-Agent": "kernel-lore-bot/1.0"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            log.warning("Failed to fetch/parse new.atom at t=%s: %s", timestamp, exc)
            return

        entries_in_page = 0
        last_updated = timestamp

        for entry_el in root.findall(_tag(ATOM_NS, "entry")):
            entry = _parse_entry(entry_el)
            if entry is None:
                continue

            entries_in_page += 1
            last_updated = entry.updated

            if entry.updated < after:
                return          # everything from here is too old

            yield entry

        if entries_in_page == 0:
            return              # empty page → done

        # Advance the cursor one second before the oldest entry on this page
        timestamp = last_updated - timedelta(seconds=1)
        time.sleep(config.REQUEST_DELAY_SECONDS)


# ------------------------------------------------------------------ #
#  Thread atom fetch + tree construction                               #
# ------------------------------------------------------------------ #

def _fetch_thread_tree(thread_atom_url: str) -> Optional[Thread]:
    """
    Fetch <url>/t.atom, parse all entries, and assemble the Thread tree.

    Entries with no thr:in-reply-to become roots. There is normally exactly
    one, but if the feed contains multiple (malformed/split thread) they are
    all collected into Thread.roots so they render as a single grouped thread
    rather than being mistaken for separate threads.
    """
    root_xml = _fetch_xml(thread_atom_url)
    if root_xml is None:
        return None

    entries: dict[str, Entry] = {}
    for entry_el in root_xml.findall(_tag(ATOM_NS, "entry")):
        entry = _parse_entry(entry_el)
        if entry is not None:
            entries[entry.id] = entry

    if not entries:
        return None

    # Separate roots from replies
    root_entries = sorted(
        [e for e in entries.values() if not e.is_reply],
        key=lambda e: e.updated,
    )

    if not root_entries:
        # Degenerate feed: no entry lacks thr:in-reply-to; treat oldest as root
        root_entries = [min(entries.values(), key=lambda e: e.updated)]
        log.debug("No root found in %s — using oldest entry as root", thread_atom_url)
    elif len(root_entries) > 1:
        log.debug(
            "%d roots found in %s — grouping under single Thread",
            len(root_entries), thread_atom_url,
        )

    # Build parent_id → [child entries] map
    children_map: dict[str, list[Entry]] = {eid: [] for eid in entries}
    for entry in entries.values():
        if entry.is_reply and entry.reply.ref in entries:
            children_map[entry.reply.ref].append(entry)

    def _build(entry: Entry) -> Node:
        node = Node(entry=entry)
        for child_entry in sorted(children_map.get(entry.id, []), key=lambda e: e.updated):
            node.children.append(_build(child_entry))
        return node

    return Thread(roots=[_build(e) for e in root_entries])


# ------------------------------------------------------------------ #
#  Public API                                                          #
# ------------------------------------------------------------------ #

def fetch_new_threads(cutoff: Optional[datetime] = None) -> list[Thread]:
    """
    Fetch all threads that have at least one new entry since `cutoff`.

    Each returned Thread:
      - Has .status set to 'new' (first root is within cutoff) or 'updated'
        (first root is older but the thread has a reply within cutoff)
      - Has fully populated .roots with recursive Node trees

    The trees contain *all* entries (old and new alike) so the caller can
    highlight which nodes are new.
    """
    if cutoff is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.LOOPBACK_HOURS)

    log.info("=== Kernel Lore scrape started (cutoff: %s) ===", cutoff.isoformat())

    # Step 1: collect all new entries from new.atom, deduplicated by ID.
    # Entries whose title matches a blacklisted keyword are dropped immediately;
    # their thread will never be fetched.
    blacklisted_ids: set[str] = set()   # IDs skipped due to blacklist
    new_entries: list[Entry] = []

    for entry in _fetch_new_entries(after=cutoff):
        if _is_blacklisted(entry.title):
            blacklisted_ids.add(entry.id)
            log.debug("Blacklisted entry: %s", entry.title)
            continue

        new_entries.append(entry)

    log.info(
        "Collected %d new entries from new.atom (%d blacklisted)",
        len(new_entries), len(blacklisted_ids),
    )

    if not new_entries:
        return []

    # Step 2: fetch each thread's /t.atom once.
    # After each fetch, register all entry IDs contained in that thread so
    # subsequent new_entries that belong to the same thread are skipped
    # without issuing another request.
    fetched_trees: list[Thread] = []
    seen_in_threads: set[str] = set()   # entry IDs already covered by a fetch

    for entry in new_entries:
        if entry.id in seen_in_threads:
            continue

        t_atom_url = f"{entry.url}/t.atom"
        log.debug("Fetching thread tree: %s", t_atom_url)
        tree = _fetch_thread_tree(t_atom_url)
        if tree is not None:
            fetched_trees.append(tree)
            # Walk every node in the tree and mark its entry ID as seen
            stack = [node for node in tree.roots]
            while stack:
                node = stack.pop()
                seen_in_threads.add(node.entry.id)
                stack.extend(node.children)

        time.sleep(config.REQUEST_DELAY_SECONDS)

    log.info("Fetched %d unique thread trees", len(fetched_trees))

    # Step 3: determine status and sort
    for tree in fetched_trees:
        tree.status = "new" if tree.roots[0].entry.updated >= cutoff else "updated"

    # Sort: new threads first, then by canonical root updated descending
    fetched_trees.sort(key=lambda t: (t.status != "new", -t.roots[0].entry.updated.timestamp()))

    log.info(
        "=== Scrape complete: %d thread(s) (%d new, %d updated) ===",
        len(fetched_trees),
        sum(1 for t in fetched_trees if t.status == "new"),
        sum(1 for t in fetched_trees if t.status == "updated"),
    )

    return fetched_trees
