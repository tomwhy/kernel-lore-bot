#!/usr/bin/env python3
"""
bot.py – entry point for the Kernel Lore Telegram bot

Usage:
    python bot.py           # start scheduler + Telegram poller (normal mode)
    python bot.py --now     # run one scrape immediately, then exit
    python bot.py --test    # dry-run: print matches, don't send anything
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import config
import subscribers
import scraper
import bot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kernel-bot")


# ------------------------------------------------------------------ #
#  Core scrape + broadcast job                                         #
# ------------------------------------------------------------------ #

def _dry_run() -> None:
    threads = scraper.fetch_new_threads(dry=True)

    if not threads:
        print("[DRY RUN] No new interesting threads found.")
        return
    print(f"\n[DRY RUN] {len(threads)} thread(s) would be sent to "
          f"{subscribers.count()} subscriber(s):\n")
    for t in threads:
        icon = "🔴" if t.label == "security" else "🟢"
        print(f"  {icon} [{t.list_name}] {t.title}")
        print(f"     by {t.author} — {t.updated.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"     {t.url}")
        print(f"     {t.in_reply_to}")
        print()


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel Lore Telegram Bot")
    parser.add_argument("--dry", action="store_true", help="Dry-run: print, don't send")
    args = parser.parse_args()

    _check_config(args)

    if args.dry:
        _dry_run()
        return

    # ---- Normal mode: poller thread + daily scheduler ----
    log.info("Starting Telegram bot…")
    bot.run_bot()


def _check_config(args) -> None:
    errors = []
    if not args.dry and config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("TELEGRAM_BOT_TOKEN is not set (config.py or Docker secret)")
    if not config.WATCHED_LISTS:
        errors.append("WATCHED_LISTS is empty — add at least one list")
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        sys.exit(1)
 

if __name__ == "__main__":
    main()
