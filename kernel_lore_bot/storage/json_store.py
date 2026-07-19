"""
JsonStore: the whole state in one file, loaded once, written atomically.

    {
      "version": 3,
      "subscribers": {
        "12345": {
          "follows": ["msgid-a@example.com"],
          "mailing_lists": ["netdev"],
          "blocked_authors": ["lkp@intel.com"]
        }
      }
    }

One file means one atomic write per mutation, so /stop cannot half-apply. Reads
are served from memory; this is safe because python-telegram-bot runs the job
queue and handlers on a single event loop, so there is exactly one owner.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from kernel_lore_bot.filters import looks_like_address, normalize_address
from kernel_lore_bot.storage.base import BaseStore, Subscriber

log = logging.getLogger(__name__)

STATE_VERSION = 3


def _migrate_blocks_to_addresses(
    chat: str, blocks: list, default_blocks: frozenset[str]
) -> set[str]:
    """
    Bring one pre-v3 "blocked_authors" list forward to address matching.

    Before v3 these held display names, matched as case-insensitive
    substrings. Under exact-address matching a name can never match
    anything, so keeping it would show the subscriber a rule that silently
    does nothing — worse than showing them it is gone. Drop those, and
    normalize the entries that already are addresses.

    A record left with NOTHING after the drop is reseeded from the
    configured defaults: that subscriber demonstrably wanted something
    muted, and the default block is the closest surviving equivalent. A
    record that was ALREADY empty is untouched — that means they removed
    everything deliberately, and handing a block back would undo a choice
    they made.
    """
    kept = {normalize_address(b) for b in blocks if looks_like_address(str(b))}
    dropped = [b for b in blocks if not looks_like_address(str(b))]
    if dropped:
        log.warning(
            "subscriber %s: dropping %d pre-v3 name block(s) that cannot match "
            "an address: %s",
            chat, len(dropped), ", ".join(repr(d) for d in dropped),
        )
    if blocks and not kept:
        log.warning(
            "subscriber %s: every block was a name — reseeding defaults %s",
            chat, sorted(default_blocks),
        )
        return set(default_blocks)
    return kept


def _subscriber_from_json(
    chat: str,
    rec: dict,
    default_lists: frozenset[str],
    default_blocks: frozenset[str],
    version: int = STATE_VERSION,
) -> Subscriber:
    """Build a Subscriber from one entry of the "subscribers" object.

    A missing key (or an explicit JSON null) means a v1 record, which
    predates the field: fall back to the configured defaults so an existing
    subscriber's digest is unchanged by the upgrade. An empty list is NOT
    missing — it means the subscriber deliberately removed everything, and
    must survive a restart.

    Each of "follows"/"mailing_lists"/"blocked_authors", when present and
    non-null, must be a JSON array. set() happily accepts any iterable, so a
    stray scalar (e.g. a string) or an object would otherwise be silently
    coerced into a bag of characters/keys instead of being rejected —
    raising here lets the per-record guard in _load_state catch, log, and
    skip just this one malformed record.
    """
    follows = rec.get("follows")
    lists = rec.get("mailing_lists")
    blocks = rec.get("blocked_authors")
    for key, value in (
        ("follows", follows),
        ("mailing_lists", lists),
        ("blocked_authors", blocks),
    ):
        if value is not None and not isinstance(value, list):
            raise ValueError(
                f"subscriber {chat!r}: {key!r} must be a JSON array, got "
                f"{type(value).__name__}: {value!r}"
            )
    if blocks is None:
        blocked = set(default_blocks)
    elif version < 3:
        blocked = _migrate_blocks_to_addresses(chat, blocks, default_blocks)
    else:
        blocked = set(blocks)

    return Subscriber(
        chat_id=int(chat),
        follows=set(follows) if follows is not None else set(),
        mailing_lists=set(default_lists) if lists is None else set(lists),
        blocked_authors=blocked,
    )


def _timestamped_backup_path(path: Path) -> Path:
    """A `.corrupt-<UTC timestamp>` sibling of `path`, made collision-safe.

    Two independent call sites in this module mint a backup name from this
    same second-granularity timestamp: the file-level corrupt-rename path
    (`os.replace`) and the partial-skip copy path (`shutil.copy2`). Within
    the same second, both would otherwise compute the IDENTICAL name — and
    whichever call lands second would silently destroy the backup the other
    one just wrote, defeating the entire point of preserving it. Appending a
    numeric suffix until the name is free removes the collision instead of
    resolving it in favor of whichever call happens to run last.

    This only avoids an existing name; it does not reserve the one it
    returns. Calling it twice with no file written in between yields the
    identical candidate both times. Safe today because every call site
    writes the file synchronously, immediately after computing the path,
    and the module docstring guarantees a single owner (no threads).
    """
    stem = path.name + "." + datetime.now(timezone.utc).strftime("corrupt-%Y%m%d%H%M%S")
    candidate = path.with_name(stem)
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{stem}-{counter}")
        counter += 1
    return candidate


def _subscriber_to_json(sub: Subscriber) -> dict:
    """The on-disk shape of one subscriber. Sorted so writes are stable."""
    return {
        "follows": sorted(sub.follows),
        "mailing_lists": sorted(sub.mailing_lists),
        "blocked_authors": sorted(sub.blocked_authors),
    }


def _load_state(
    path: Path,
    default_lists: frozenset[str],
    default_blocks: frozenset[str],
) -> dict[int, Subscriber]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # A missing or non-integer version means a file written before the
        # field was trustworthy. Treat it as the oldest schema: migrating is
        # safe to repeat (an address survives it unchanged), whereas
        # assuming "current" would leave stale name blocks in place forever.
        raw_version = raw.get("version")
        version = raw_version if isinstance(raw_version, int) else 1

        subs: list[Subscriber] = []
        total = 0
        skipped = 0
        for chat, rec in raw.get("subscribers", {}).items():
            total += 1
            try:
                subs.append(
                    _subscriber_from_json(
                        chat, rec, default_lists, default_blocks, version
                    )
                )
            except (ValueError, AttributeError, TypeError) as exc:
                # One malformed record (a non-iterable or otherwise bogus
                # "mailing_lists"/"blocked_authors"/"follows") must not take
                # every OTHER subscriber's valid follows down with it. Log
                # loudly and skip just this record; whether the file itself
                # needs backing up is decided below, once we know how many
                # records were affected in total.
                skipped += 1
                log.error(
                    "Could not parse subscriber %r in %s: %s — skipping",
                    chat, path, exc,
                )

        if total and skipped == total:
            # EVERY record failed to parse. That is no longer "one bad
            # row" — it is either a genuinely corrupt file or a systemic
            # bug in Subscriber construction that would silently swallow
            # every subscriber with no exception and no backup (see the
            # per-record except above, which is deliberately broad).
            # Route it into the file-level except below, which preserves
            # the original bytes under a timestamped backup instead of
            # starting empty with only a log line to show for it.
            raise ValueError(
                f"all {total} subscriber record(s) in {path} failed to parse"
            )

        if skipped:
            # Some, but not all, records were skipped. _flush only ever
            # serializes what made it into memory, so the very next
            # mutation would rewrite state.json with the skipped records
            # permanently gone — silent, unrecoverable data loss. Preserve
            # the original bytes now, before that can happen. This is a
            # copy, not a rename: the live file must keep serving the
            # records that DID load.
            #
            # A bot that never reaches a mutation (a startup crash-loop, or
            # simply no traffic) would otherwise copy the ENTIRE state file
            # again on every single restart, even though the bad record
            # never changes. If an existing backup already holds
            # byte-identical content, the current file is already fully
            # preserved — skip the copy instead of piling up duplicates.
            original_bytes = path.read_bytes()
            existing_backups = path.parent.glob(path.name + ".corrupt-*")

            def _same_content(backup: Path) -> bool:
                # A sibling matching the glob is not guaranteed to be a
                # readable file — it could be a directory, a broken
                # symlink, or something permission-denied by the OS. Any
                # OSError here means "can't prove a match", so fall through
                # and let a fresh backup be written rather than letting one
                # bad sibling abort the whole scan (and the bot's startup).
                try:
                    return backup.read_bytes() == original_bytes
                except OSError:
                    return False

            if any(_same_content(b) for b in existing_backups):
                log.error(
                    "Skipped %d of %d subscriber record(s) in %s — original "
                    "already preserved in an existing backup, not copying again",
                    skipped, total, path,
                )
            else:
                backup = _timestamped_backup_path(path)
                try:
                    shutil.copy2(path, backup)
                    log.error(
                        "Skipped %d of %d subscriber record(s) in %s — original "
                        "preserved at %s",
                        skipped, total, path, backup,
                    )
                except OSError as copy_exc:
                    log.error(
                        "Skipped %d of %d subscriber record(s) in %s — AND "
                        "could not back it up (%s)",
                        skipped, total, path, copy_exc,
                    )

        return {sub.chat_id: sub for sub in subs}
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        # The file exists but is unreadable (e.g. a crash landed the rename
        # before an fsync). Do NOT silently discard it — every subscriber and
        # follow would be gone with only a warning to show for it. Preserve
        # the bytes under a timestamped backup name so the data stays
        # recoverable on disk, log loudly, and only then continue empty.
        backup = _timestamped_backup_path(path)
        try:
            os.replace(path, backup)
            log.error(
                "Could not parse %s: %s — original preserved at %s, starting fresh",
                path, exc, backup,
            )
        except OSError as replace_exc:
            log.error(
                "Could not parse %s: %s — AND could not back it up (%s), starting fresh",
                path, exc, replace_exc,
            )
        return {}


class JsonStore(BaseStore):
    """Store backed by a single JSON file."""

    def __init__(
        self,
        path: Path,
        default_lists: Iterable[str] = (),
        default_blocks: Iterable[str] = (),
    ) -> None:
        self._path = Path(path)
        lists = frozenset(default_lists)
        blocks = frozenset(default_blocks)
        super().__init__(_load_state(self._path, lists, blocks), lists, blocks)

    def _flush(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "subscribers": {
                str(chat): _subscriber_to_json(self._subs[chat])
                for chat in sorted(self._subs)
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        # Flush and fsync the temp file's contents to disk *before* the
        # rename. os.replace() is atomic w.r.t. the directory entry, but
        # without an fsync the data itself can still be sitting in the OS
        # page cache when the rename lands — a host/container crash right
        # after can leave state.json truncated or zero-length even though
        # the rename "completed".
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
