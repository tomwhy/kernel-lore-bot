"""
Entry point.

Usage:
    python -m kernel_lore_bot          # scheduler + Telegram poller
    python -m kernel_lore_bot --dry    # print what would be sent; send nothing
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

from kernel_lore_bot.delivery.app import run_bot
from kernel_lore_bot.delivery.formatting import format_thread
from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.http import RequestsClient
from kernel_lore_bot.models import Classified, ThreadStatus
from kernel_lore_bot.progress import TqdmProgress
from kernel_lore_bot.settings import PLACEHOLDER_TOKEN, Settings, load_settings
from kernel_lore_bot.sources.lore.index import ListRegistry
from kernel_lore_bot.sources.lore.source import LoreSource
from kernel_lore_bot.storage import JsonStore, Store

log = logging.getLogger("kernel-bot")


def build_components(settings: Settings) -> tuple[Store, LoreSource, ListRegistry]:
    """Construct the real, I/O-touching implementations."""
    client = RequestsClient(
        timeout=settings.request_timeout,
        max_attempts=settings.request_attempts,
        backoff=settings.request_backoff,
        min_interval=settings.request_min_interval,
    )
    store = JsonStore(
        settings.state_file,
        default_lists=settings.mailing_lists,
        default_blocks=settings.blocked_authors,
    )
    source = LoreSource(client=client, progress=TqdmProgress())
    # Starts on the configured lists so the bot works before the first
    # successful manifest fetch. Deliberately NOT refreshed here:
    # build_components must stay free of network I/O so it is testable, and
    # the first scheduled scrape runs with `first=0` — i.e. immediately at
    # startup — so the real index lands within seconds anyway.
    registry = ListRegistry(client, fallback=settings.mailing_lists)
    return store, source, registry


def check_config(settings: Settings, dry: bool) -> list[str]:
    """Return a list of configuration errors. Empty means good to go."""
    errors = []
    if not dry and settings.telegram_bot_token == PLACEHOLDER_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set (env var or Docker secret)")
    return errors


def format_dry_run(classified: Sequence[Classified], cutoff: datetime) -> str:
    """Render what a real run would send, using the real formatter."""
    if not classified:
        return "[DRY RUN] No new threads found."

    new = [c for c in classified if c.status is ThreadStatus.NEW]
    updated = [c for c in classified if c.status is ThreadStatus.UPDATED]

    lines = [f"[DRY RUN] {len(new)} new thread(s) would be broadcast:", ""]
    for item in new:
        lines.append(format_thread(item, cutoff))
        lines.append("")

    if updated:
        lines.append(
            f"[DRY RUN] {len(updated)} updated thread(s) — followers only:"
        )
        lines.append("")
        for item in updated:
            lines.append(format_thread(item, cutoff))
            lines.append("")

    return "\n".join(lines)


def _configure_console() -> None:
    """
    Force UTF-8 on stdout.

    The digest is full of emoji and the default Windows console encoding here is
    cp1255, which cannot encode them: printing would raise UnicodeEncodeError.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configure_console()

    parser = argparse.ArgumentParser(description="Kernel Lore Telegram Bot")
    parser.add_argument("--dry", action="store_true", help="Dry-run: print, don't send")
    args = parser.parse_args(argv)

    settings = load_settings()

    errors = check_config(settings, dry=args.dry)
    if errors:
        for err in errors:
            log.error("Config error: %s", err)
        return 1

    store, source, registry = build_components(settings)

    if args.dry:
        broadcaster = Broadcaster(settings, store, source)
        cutoff = broadcaster.cutoff(datetime.now(timezone.utc))
        followed_ids = sorted(store.all_followed_threads())
        # settings.mailing_lists, not store.all_mailing_lists(): production
        # (_run_locked) scrapes the union of every subscriber's lists, but a
        # --dry run is commonly the very first thing anyone runs, before any
        # subscriber exists, when store.all_mailing_lists() would be empty
        # and this would preview nothing at all. Using the configured lists
        # instead means --dry always previews *something* on a fresh
        # install -- at the cost of being a different code path than
        # production once real subscribers with their own lists exist.
        print(
            format_dry_run(
                broadcaster.collect(cutoff, settings.mailing_lists, followed_ids), cutoff
            )
        )
        return 0

    log.info("Starting Telegram bot…")
    run_bot(settings, store, source, registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
