"""
scraper.py – fetch and classify kernel lore Atom feeds
"""

from __future__ import annotations

import email as _email
import gzip
import logging
import re
import time
import tqdm
import urllib
import pathlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional, Generator

import requests

import config

log = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
THREAD_NS = "http://purl.org/syndication/thread/1.0"
KERNEL_LORE_URL = "https://lore.kernel.org/all/new.atom"
LORE_BASE_URL   = "https://lore.kernel.org/all"


# ------------------------------------------------------------------ #
#  Data classes                                                        #
# ------------------------------------------------------------------ #

@dataclass
class Reply:
    """The In-Reply-To reference on a non-root entry."""
    ref: str    # Message-ID of the parent (with angle brackets)


@dataclass
class Entry:
    """A single email message parsed from an mbox message."""
    id: str             # Message-ID (with angle brackets)
    title: str
    url: str            # https://lore.kernel.org/all/<msgid>/
    author: str
    updated: datetime
    mailing_list: str
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
    A single email thread reconstructed from its mbox archive.

    `roots`        – one or more root nodes (messages with no In-Reply-To
                     that resolves to another message in the thread).
                     Normally exactly one; >1 signals a split or malformed
                     thread.
    `status`       – 'new' | 'updated'

    Convenience properties delegate to roots[0] for sorting/header use.
    """
    roots: list[Node]
    status: str = ""

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
    
    @property
    def mailing_list(self) -> str:
        return self.roots[0].entry.mailing_list


# ------------------------------------------------------------------ #
#  XML helpers (used for new.atom pagination only)                     #
# ------------------------------------------------------------------ #

def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"

# ------------------------------------------------------------------ #
#  mbox parsing                                                        #
# ------------------------------------------------------------------ #

# Matches the "From <sender> <date>" separator line that opens each
# message in an mbox file.
_MBOX_SEP_RE = re.compile(r"^From ", re.MULTILINE)

# Matches a List-Id header value per RFC 2919:
#   optional-display-name <local-part.domain>
_LIST_ID_RE = re.compile(r"<([^>]+)>")


def _parse_list_id(header_value: str) -> str:
    m = _LIST_ID_RE.search(header_value)
    spec = m.group(1) if m else header_value
    if spec in config.MAILING_LIST_NAMES:
        return config.MAILING_LIST_NAMES[spec]
    if "@" in spec:
        return spec.split("@")[0]
    return spec.split(".")[0]


def _parse_mbox_message(msg: Message) -> Optional[Entry]:
    try:
        msgid = (msg["Message-ID"] or "").strip().strip("<>")
        if not msgid:
            return None

        subject = (msg["Subject"] or "").strip()
        subject = _email.header.decode_header(subject)
        subject = "".join(
            (part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part)
            for part, enc in subject
        )

        raw_from = msg["From"] or ""
        display_name, addr = parseaddr(raw_from)
        author = display_name.strip() or addr.strip() or "Unknown"

        raw_date = msg["Date"] or ""
        try:
            updated = parsedate_to_datetime(raw_date)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except Exception:
            updated = datetime.now(timezone.utc)

        list_id = msg["List-Id"] or ""
        address = _parse_list_id(list_id)
        mailing_list = config.MAILING_LIST_NAMES.get(address)

        in_reply_to = (msg["In-Reply-To"] or "").strip().strip("<>")
        reply = Reply(ref=in_reply_to) if in_reply_to else None

        url = f"{LORE_BASE_URL}/{msgid}"

        return Entry(
            id=msgid,
            title=subject,
            url=url,
            author=author,
            updated=updated,
            reply=reply,
            mailing_list=mailing_list or address,
        )
    except Exception as exc:
        log.debug("Skipping malformed mbox message: %s", exc)
        return None


_MBOX_PROGRESS_THRESHOLD = 256 * 1024   # show bar only for files > 256 KB


def _fetch_mbox(url: str) -> Optional[str]:
    """
    Fetch a .mbox.gz URL, decompress it, and return the plaintext mbox.
    Shows a tqdm progress bar when the download exceeds
    _MBOX_PROGRESS_THRESHOLD bytes.
    Returns None on any fetch or decompression error.
    """
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": "kernel-lore-bot/1.0"},
            stream=True,
        )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0)) or None
        show_bar = total is None or total > _MBOX_PROGRESS_THRESHOLD

        chunks: list[bytes] = []
        downloaded = 0

        with tqdm.tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="  ↳ mbox",
            leave=False,
            dynamic_ncols=True,
            disable=not show_bar,
        ) as mbox_bar:
            for chunk in resp.iter_content(chunk_size=65536):
                chunks.append(chunk)
                downloaded += len(chunk)
                mbox_bar.update(len(chunk))

        raw_bytes = b"".join(chunks)

        try:
            raw = gzip.decompress(raw_bytes)
        except gzip.BadGzipFile:
            raw = raw_bytes             # server sent uncompressed mbox

        return raw.decode("utf-8", errors="replace")

    except requests.RequestException as exc:
        log.warning("HTTP error fetching mbox %s: %s", url, exc)
    return None


def iter_mbox_emails(mbox_text) -> Generator[_email.Message, None, None]:
    seps = _MBOX_SEP_RE.finditer(mbox_text)
    start = next(seps).start()
    for next_sep in seps:
        yield _email.message_from_string(mbox_text[start:next_sep.start()])
        start = next_sep.start()
    yield _email.message_from_string(mbox_text[start:])

# ------------------------------------------------------------------ #
#  Thread mbox fetch + tree construction                               #
# ------------------------------------------------------------------ #

def _fetch_thread_tree(entry_id: str) -> Optional[Thread]:
    mbox_url = f"https://lore.kernel.org/all/{entry_id}/t.mbox.gz"
    mbox_text = _fetch_mbox(mbox_url)
    if mbox_text is None:
        return None

    entries: list[Entry] = list(filter(lambda entry: entry is not None, map(_parse_mbox_message, iter_mbox_emails(mbox_text))))
    if not entries:
        log.debug("No valid messages parsed from mbox at %s", mbox_url)
        return None

    root_entries = [e for e in entries if not e.is_reply]
    if not root_entries:
        root_entries = [entries[0]]
        log.debug("No root found in %s — using first message as root", mbox_url)
    elif len(root_entries) > 1:
        log.debug(
            "%d roots found in %s — grouping under single Thread",
            len(root_entries), mbox_url,
        )

    children_map: dict[str, list[Entry]] = {e.id: [] for e in entries}
    for entry in entries:
        if entry.is_reply and entry.reply.ref in children_map:
            children_map[entry.reply.ref].append(entry)

    def _build(entry: Entry) -> Node:
        node = Node(entry=entry)
        for child in sorted(children_map.get(entry.id, []), key=lambda e: e.updated):
            node.children.append(_build(child))
        return node

    return Thread(
        roots=[_build(e) for e in root_entries],
    )


# ------------------------------------------------------------------ #
#  new.atom pagination                                                 #
# ------------------------------------------------------------------ #

def _fetch_new_entries(after: datetime) -> Generator[str, None, None]:
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
            updated_raw = (entry_el.findtext(_tag(ATOM_NS, "updated")) or "").strip()
            updated = datetime.fromisoformat(updated_raw)
            
            link_el    = entry_el.find(_tag(ATOM_NS, "link"))
            thread_url = link_el.get("href", "") if link_el is not None else ""
            entry_id = pathlib.Path(urllib.parse.urlparse(thread_url).path).name

            entries_in_page += 1
            last_updated = updated

            if updated < after:
                return

            yield entry_id

        if entries_in_page == 0:
            return

        timestamp = last_updated - timedelta(seconds=1)


# ------------------------------------------------------------------ #
#  Filtering                                                           #
# ------------------------------------------------------------------ #

def _is_blocked(thread: Thread) -> bool:
    """Return True if the thread matches any configured blocklist rule."""
    if thread.author.lower() in config.BLOCKED_AUTHORS:
        log.debug("Blocked by author filter: %r (%s)", thread.title, thread.author)
        return True

    if thread.mailing_list not in config.MAILLING_LISTS:
        log.debug("Blocked by list filter: %r (%s)", thread.title, thread.mailing_list)
        return True

    return False

# ------------------------------------------------------------------ #
#  Public API                                                          #
# ------------------------------------------------------------------ #

def fetch_new_threads(cutoff: Optional[datetime] = None) -> list[Thread]:
    if cutoff is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.LOOPBACK_HOURS)

    log.info("=== Kernel Lore scrape started (cutoff: %s) ===", cutoff.isoformat())

    fetched_trees: list[Thread] = []
    seen: set[str] = set()

    with tqdm.tqdm(
        desc="Fetching threads",
        unit=" entries",
        dynamic_ncols=True,
    ) as pbar:
        for entry in _fetch_new_entries(after=cutoff):
            pbar.update(1)
            if entry in seen:
                continue
            
            thread = _fetch_thread_tree(entry)
            if thread is None:
                seen.add(entry)
                continue

            fetched_trees.append(thread)
            pbar.set_postfix(threads=len(fetched_trees))

            stack: list[Node] = list(thread.roots)
            while stack:
                node = stack.pop()
                seen.add(node.entry.id)
                stack.extend(node.children)

    log.info("Fetched %d unique thread trees", len(fetched_trees))

    filtered = [t for t in fetched_trees if not _is_blocked(t)]
    if len(filtered) < len(fetched_trees):
        log.info(
            "Filtered out %d thread(s) by blocklist (%d remaining)",
            len(fetched_trees) - len(filtered),
            len(filtered),
        )
        fetched_trees = filtered

    for tree in fetched_trees:
        tree.status = (
            "new" if tree.roots and tree.roots[0].entry.updated >= cutoff
            else "updated"
        )

    fetched_trees.sort(
        key=lambda t: (t.status != "new", -t.roots[0].entry.updated.timestamp())
    )

    log.info(
        "=== Scrape complete: %d thread(s) (%d new, %d updated) ===",
        len(fetched_trees),
        sum(1 for t in fetched_trees if t.status == "new"),
        sum(1 for t in fetched_trees if t.status == "updated"),
    )

    return fetched_trees