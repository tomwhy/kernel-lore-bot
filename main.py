#!/usr/bin/env python3
"""
main.py – entry point for the Kernel Lore Telegram bot

Usage:
    python main.py          # start scheduler + Telegram poller (normal mode)
    python main.py --dry    # dry-run: print matches, don't send anything
"""

from __future__ import annotations

import argparse
import logging
import sys

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
#  Dry run                                                             #
# ------------------------------------------------------------------ #

def _dry_run() -> None:
    threads = scraper.fetch_new_threads()

    if not threads:
        print("[DRY RUN] No new threads found.")
        return

    new_threads     = [t for t in threads if t.status == "new"]
    updated_threads = [t for t in threads if t.status == "updated"]

    sub_count = subscribers.count()

    print(
        f"\n[DRY RUN] {len(new_threads)} new thread(s) would be sent to "
        f"{sub_count} subscriber(s):\n"
    )
    for t in new_threads:
        print(f"  🆕 {t.title}")
        print(f"     by {t.author} — {t.updated.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"     📬 {t.mailing_list}")
        print(f"     {t.url}")
        if len(t.roots) > 1:
            print(f"     ({len(t.roots)} roots)")
        print()

    if updated_threads:
        print(
            f"[DRY RUN] {len(updated_threads)} updated thread(s) — "
            f"would notify followers only:\n"
        )
        for t in updated_threads:
            import follows as _follows
            follower_count = len(_follows.get_followers(t.id))
            print(f"  🔄 {t.title}")
            print(f"     by {t.author} — {t.updated.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"     📬 {t.mailing_list}")
            print(f"     {t.url}")
            print(f"     👥 {follower_count} follower(s)")
            if len(t.roots) > 1:
                print(f"     ({len(t.roots)} roots)")
            print()


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

def _check_config(args: argparse.Namespace) -> None:
    errors = []
    if not args.dry and config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        errors.append("TELEGRAM_BOT_TOKEN is not set (config.py or Docker secret)")
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel Lore Telegram Bot")
    parser.add_argument("--dry", action="store_true", help="Dry-run: print, don't send")
    args = parser.parse_args()

    _check_config(args)

    if args.dry:
        _dry_run()
        return

    log.info("Starting Telegram bot…")
    bot.run_bot()


if __name__ == "__main__":
    main()
