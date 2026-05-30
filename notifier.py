"""
notifier.py – format and broadcast Telegram messages to all subscribers
"""

from __future__ import annotations

import logging
import time

import requests

import config
import subscribers
from scraper import Thread

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

LABEL_ICON = {
    "security": "🔴",
    "feature":  "🟢",
}

LABEL_TAG = {
    "security": "#security #CVE",
    "feature":  "#feature #kernel",
}


# ------------------------------------------------------------------ #
#  Formatting                                                          #
# ------------------------------------------------------------------ #

def _escape(text: str) -> str:
    """Escape Markdown v2 special chars."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_thread(thread: Thread) -> str:
    icon = LABEL_ICON.get(thread.label, "⚪")
    tags = LABEL_TAG.get(thread.label, "")
    date_str = thread.updated.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"{icon} *{_escape(thread.title)}*",
        "",
        f"📋 List: `{_escape(thread.list_name)}`",
        f"👤 Author: {_escape(thread.author)}",
        f"🕐 {_escape(date_str)}",
    ]

    if thread.summary:
        snip = thread.summary[:200].replace("\n", " ")
        lines += ["", f"_{_escape(snip)}_"]

    lines += ["", f"[🔗 View thread]({thread.url})", "", tags]
    return "\n".join(lines)


def _format_header(count: int) -> str:
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return (
        f"🐧 *Kernel Lore Daily Digest*\n"
        f"_{_escape(now)}_\n\n"
        f"Found *{count}* interesting thread(s) today\\."
    )


# ------------------------------------------------------------------ #
#  Low-level send (single chat)                                        #
# ------------------------------------------------------------------ #

def send_to(chat_id: int, text: str) -> bool:
    """Send one message to one chat. Returns True on success."""
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Telegram send failed (chat=%d): %s", chat_id, exc)
        return False


def _broadcast(text: str, chat_ids: set[int]) -> int:
    """Send one message to every subscriber. Returns count of successes."""
    sent = 0
    blocked: list[int] = []

    for chat_id in chat_ids:
        ok = send_to(chat_id, text)
        if ok:
            sent += 1
        else:
            # 403 / chat not found means the user blocked the bot
            blocked.append(chat_id)
        time.sleep(0.05)   # stay well under Telegram's 30 msg/s global limit

    # Auto-unsubscribe chats that blocked the bot
    if blocked:
        subscribers.remove_many(blocked)
        log.info("Auto-removed %d blocked/unreachable chat(s)", len(blocked))

    return sent


# ------------------------------------------------------------------ #
#  Public broadcast API                                                #
# ------------------------------------------------------------------ #

def send_threads(threads: list[Thread]) -> int:
    """
    Broadcast the daily digest to all subscribers.
    Returns total messages sent (header + threads × subscribers).
    """
    chat_ids = subscribers.load()

    if not chat_ids:
        log.info("No subscribers yet — nothing to send.")
        return 0

    log.info("Broadcasting digest (%d threads) to %d subscriber(s)",
             len(capped), len(chat_ids))

    total_sent = 0

    # 1. Header
    header = _format_header(len(capped))
    total_sent += _broadcast(header, chat_ids)
    time.sleep(0.5)

    # 2. Individual threads
    for thread in threads:
        msg = _format_thread(thread)
        total_sent += _broadcast(msg, chat_ids)
        time.sleep(0.5)

    log.info("Broadcast complete: %d message(s) sent across all subscribers", total_sent)
    return total_sent
