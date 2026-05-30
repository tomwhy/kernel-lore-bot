#!/usr/bin/env python3
"""
bot.py – entry point for the Kernel Lore Telegram bot

Usage:
    python bot.py           # start scheduler (runs daily per config)
    python bot.py --now     # run one scrape immediately, then exit
    python bot.py --test    # dry-run: print matches, don't send Telegram msgs
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import schedule

import config
import state
from notifier import send_threads
from scraper import Thread, fetch_all_feeds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kernel-bot")


# ------------------------------------------------------------------ #
#  Core job                                                            #
# ------------------------------------------------------------------ #

def run_job(dry_run: bool = False) -> None:
    log.info("=== Kernel Lore scrape started ===")

    first_run = state.is_first_run()
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
    log.info("New interesting threads: %d", len(new_threads))

    if dry_run:
        _print_dry_run(new_threads)
    else:
        if new_threads:
            send_threads(new_threads)
        else:
            log.info("Nothing new to send today.")

    # Update state
    seen.update(t.id for t in new_threads)
    by_id = {t.id: t for t in all_interesting}
    seen = state.prune_old(seen, by_id)
    state.save_seen(seen)

    log.info("=== Scrape complete ===")


def _print_dry_run(threads: list[Thread]) -> None:
    if not threads:
        print("[DRY RUN] No new interesting threads found.")
        return

    print(f"\n[DRY RUN] {len(threads)} thread(s) would be sent:\n")
    for t in threads:
        icon = "🔴" if t.label == "security" else "🟢"
        print(f"  {icon} [{t.list_name}] {t.title}")
        print(f"     by {t.author} — {t.updated.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"     {t.url}")
        print()


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel Lore Telegram Bot")
    parser.add_argument("--now",  action="store_true", help="Run once immediately and exit")
    parser.add_argument("--test", action="store_true", help="Dry-run: don't send Telegram messages")
    args = parser.parse_args()

    _check_config()

    if args.test:
        log.info("Dry-run mode enabled.")
        run_job(dry_run=True)
        return

    if args.now:
        run_job()
        return

    # ---- Scheduler mode ----
    log.info("Scheduling job: cron = %s", config.SCHEDULE_CRON)
    _schedule_from_cron(config.SCHEDULE_CRON, run_job)

    log.info("Bot is running. Press Ctrl-C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Stopped.")


def _check_config() -> None:
    errors = []
    if config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("TELEGRAM_BOT_TOKEN is not set in config.py")
    if config.TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
        errors.append("TELEGRAM_CHAT_ID is not set in config.py")
    if not config.WATCHED_LISTS:
        errors.append("WATCHED_LISTS is empty — add at least one list")
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        sys.exit(1)


def _schedule_from_cron(cron: str, job) -> None:
    """
    Parse a simple daily cron expression "M H * * *" and schedule with
    the `schedule` library.  Only minute/hour fields are used; the bot
    is designed for at-most-once-daily runs.
    """
    parts = cron.strip().split()
    if len(parts) < 2:
        raise ValueError(f"Unsupported cron expression: {cron!r}")
    minute, hour = parts[0], parts[1]
    time_str = f"{int(hour):02d}:{int(minute):02d}"
    log.info("Job scheduled daily at %s UTC", time_str)
    schedule.every().day.at(time_str, "UTC").do(job)


if __name__ == "__main__":
    main()
