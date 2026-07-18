"""
Pure mbox parsing. This module performs no I/O.

lore serves each thread as a gzipped mbox at
`<base>/all/<message-id>/t.mbox.gz`, using mboxrd: messages are separated by a
constant `From mboxrd@z ...` line, and body lines beginning with "From " are
escaped to ">From ". That is why splitting on `^From ` is safe here.
"""

from __future__ import annotations

import email
import email.header
import logging
import re
from datetime import datetime, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator, Optional

from kernel_lore_bot.models import Entry, Node, Reply, Thread

log = logging.getLogger(__name__)

LORE_BASE_URL = "https://lore.kernel.org"

_MBOX_SEP_RE = re.compile(r"^From ", re.MULTILINE)


def iter_messages(mbox_text: str) -> Iterator[Message]:
    """
    Split an mbox into messages.

    Yields nothing when the text contains no separator line — an empty body or
    an HTML error page from lore must not raise.
    """
    starts = [m.start() for m in _MBOX_SEP_RE.finditer(mbox_text)]
    if not starts:
        return
    bounds = starts + [len(mbox_text)]
    for i in range(len(starts)):
        yield email.message_from_string(mbox_text[bounds[i]:bounds[i + 1]])


def decode_header_value(raw: str) -> str:
    """Decode an RFC 2047 encoded header into plain text."""
    parts = email.header.decode_header(raw)
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in parts
    )


def parse_message(msg: Message, base_url: str = LORE_BASE_URL) -> Optional[Entry]:
    """Convert one mbox message into an Entry, or None if it is unusable."""
    try:
        msgid = (msg["Message-ID"] or "").strip().strip("<>")
        if not msgid:
            return None

        title = decode_header_value((msg["Subject"] or "").strip())

        # Split the raw (still RFC 2047-encoded) header first, then decode
        # only the display-name part. Decoding before splitting is unsafe:
        # a decoded display name can itself contain '<', '>', '"', or ','
        # (e.g. an encoded-word that decodes to `Foo <Bar>`), which would
        # confuse parseaddr's address-vs-display-name tokenizing. The
        # address portion is always plain ASCII per RFC 5322, so it needs
        # no decoding regardless of order.
        display_name, addr = parseaddr(msg["From"] or "")
        author = decode_header_value(display_name).strip() or addr.strip() or "Unknown"

        try:
            updated = parsedate_to_datetime(msg["Date"] or "").astimezone(timezone.utc)
        except Exception:
            updated = datetime.now(timezone.utc)

        in_reply_to = (msg["In-Reply-To"] or "").strip().strip("<>")

        return Entry(
            id=msgid,
            title=title,
            url=f"{base_url}/all/{msgid}",
            author=author,
            updated=updated,
            reply=Reply(ref=in_reply_to) if in_reply_to else None,
        )
    except Exception as exc:  # noqa: BLE001 - one bad message must not kill a thread
        log.debug("Skipping malformed mbox message: %s", exc)
        return None


def build_thread(entries: list[Entry], mailing_list: str = "") -> Optional[Thread]:
    """
    Assemble entries into a thread tree.

    The tree is built by walking outward from the real roots — entries that
    are not replies, whose In-Reply-To does not resolve inside this mbox, or
    whose In-Reply-To points at themselves — while tracking which entries have
    already been reached. Any entry never reached this way is promoted to a
    root too, in document order. This covers two cases that a simple "no
    parent in this mbox" root rule misses: a disconnected subgraph whose refs
    only resolve among themselves (it would otherwise vanish from the tree
    entirely), and a pure reference cycle with no real root at all (it would
    otherwise recurse forever). More than one real root signals a split
    thread and is kept as-is.
    """
    if not entries:
        return None

    by_id = {e.id: e for e in entries}
    children_map: dict[str, list[Entry]] = {e.id: [] for e in entries}

    for entry in entries:
        if entry.is_reply:
            ref = entry.reply.ref
            if ref in by_id and ref != entry.id:
                children_map[ref].append(entry)

    def is_real_root(e: Entry) -> bool:
        return not e.is_reply or e.reply.ref not in by_id or e.reply.ref == e.id

    visited: set[str] = set()

    def _build(entry: Entry) -> Node:
        visited.add(entry.id)
        kids = sorted(children_map.get(entry.id, []), key=lambda e: e.updated)
        return Node(
            entry=entry,
            children=tuple(_build(k) for k in kids if k.id not in visited),
        )

    roots = [e for e in entries if is_real_root(e)]
    if len(roots) > 1:
        log.debug("%d roots found — grouping under a single Thread", len(roots))

    root_nodes = [_build(r) for r in roots]

    # Anything not reached from a real root is its own disconnected piece — a
    # subgraph whose refs resolve only within itself, or a pure cycle.
    # Promote it too, in document order, instead of dropping it or recursing
    # forever looking for a parent that doesn't exist.
    for e in entries:
        if e.id not in visited:
            log.debug("Entry %s unreachable from any root — promoting to root", e.id)
            root_nodes.append(_build(e))

    lists = frozenset({mailing_list}) if mailing_list else frozenset()
    return Thread(roots=tuple(root_nodes), mailing_lists=lists)


def parse_thread(
    mbox_text: str, mailing_list: str = "", base_url: str = LORE_BASE_URL
) -> Optional[Thread]:
    """Parse a whole mbox into a Thread, or None if nothing usable is present."""
    entries = [
        e
        for e in (parse_message(msg, base_url) for msg in iter_messages(mbox_text))
        if e is not None
    ]
    return build_thread(entries, mailing_list)
