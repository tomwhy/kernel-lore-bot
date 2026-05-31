"""
scraper.py – fetch and classify kernel lore Atom feeds
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

import config
import state

log = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"


@dataclass
class Thread:
    """A single email thread parsed from an Atom entry."""

    id: str
    title: str
    url: str
    author: str
    updated: datetime
    list_name: str
    label: str = ""          # "security" | "feature" | ""
    summary: str = ""


def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def _classify(title: str) -> str:
    """Return 'security', 'feature', or '' based on keyword matching."""
    t = title.lower()

    # Security takes priority
    for kw in config.SECURITY_KEYWORDS:
        if kw.lower() in t:
            return "security"

    for kw in config.FEATURE_KEYWORDS:
        if kw.lower() in t:
            return "feature"

    return ""


def fetch_feed(list_name: str, url: str) -> list[Thread]:
    """Fetch one Atom feed and return classified Thread objects."""
    threads: list[Thread] = []

    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": "kernel-lore-bot/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return threads

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        log.warning("XML parse error for %s: %s", url, exc)
        return threads

    for entry in root.findall(_tag(ATOM_NS, "entry")):
        try:
            eid = (entry.findtext(_tag(ATOM_NS, "id")) or "").strip()
            title = (entry.findtext(_tag(ATOM_NS, "title")) or "").strip()
            updated_raw = (entry.findtext(_tag(ATOM_NS, "updated")) or "").strip()

            # URL: prefer <link rel="alternate">
            link_el = entry.find(_tag(ATOM_NS, "link[@rel='alternate']"))
            if link_el is None:
                link_el = entry.find(_tag(ATOM_NS, "link"))
            thread_url = (link_el.get("href") if link_el is not None else "") or ""

            # Author
            author_el = entry.find(_tag(ATOM_NS, "author"))
            if author_el is not None:
                author = (author_el.findtext(_tag(ATOM_NS, "name")) or "").strip()
            else:
                author = "unknown"

            # Summary / content
            summary = (
                entry.findtext(_tag(ATOM_NS, "summary"))
                or entry.findtext(_tag(ATOM_NS, "content"))
                or ""
            ).strip()[:300]

            # Parse date
            updated: datetime
            try:
                updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            except ValueError:
                updated = datetime.now(timezone.utc)

            label = _classify(title)

            threads.append(
                Thread(
                    id=eid,
                    title=title,
                    url=thread_url,
                    author=author,
                    updated=updated,
                    list_name=list_name,
                    label=label,
                    summary=summary,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Skipping malformed entry: %s", exc)
            continue

    log.info("Fetched %d entries from %s (%d interesting)", len(threads),
             list_name, sum(1 for t in threads if t.label))
    return threads


def fetch_all_feeds() -> list[Thread]:
    """Fetch every configured list and return only interesting threads."""
    interesting: list[Thread] = []

    for list_name, url in config.WATCHED_LISTS:
        threads = fetch_feed(list_name, url)
        interesting.extend(t for t in threads if t.label)
        time.sleep(config.REQUEST_DELAY_SECONDS)

    # Sort: security first, then by date desc
    interesting.sort(key=lambda t: (t.label != "security", -t.updated.timestamp()))
    return interesting


def fetch_new_threads(dry: bool = False) -> list[Thread]:
    log.info("=== Kernel Lore scrape started ===")

    seen = state.load_seen()
    all_interesting = fetch_all_feeds()

    # Get new threads from the last 24 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.LOOPBACK_HOURS)
    log.info("limiting to threads newer than %dh (cutoff %s)",
             config.LOOPBACK_HOURS, cutoff.strftime("%Y-%m-%d %H:%M"))
    new_threads = filter(lambda t: t.updated >= cutoff, all_interesting)

    # Filter out already-seen threads
    new_threads = filter(lambda t: t.id not in seen, new_threads)
    new_threads = list(new_threads)
    
    # Persist state
    if not dry:
        seen.update(t.id for t in new_threads)
        by_id = {t.id: t for t in all_interesting}
        seen = state.prune_old(seen, by_id)
        state.save_seen(seen)

    log.info("New interesting threads: %d", len(new_threads))
    log.info("=== Scrape complete ===")
    return new_threads
