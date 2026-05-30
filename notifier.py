"""
notifier.py – send Telegram messages for new interesting threads
"""

from __future__ import annotations

import logging
import time

import requests

import config
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


def _escape(text: str) -> str:
    """Escape Markdown v2 special chars."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_message(thread: Thread) -> str:
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

    lines += [
        "",
        f"[🔗 View thread]({thread.url})",
        "",
        tags,
    ]

    return "\n".join(lines)


def send_message(text: str) -> bool:
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def send_digest_header(count: int) -> None:
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    text = (
        f"🐧 *Kernel Lore Daily Digest*\n"
        f"_{now}_\n\n"
        f"Found *{count}* interesting thread(s) today\\."
    )
    send_message(text)


def send_threads(threads: list[Thread]) -> int:
    """Send up to MAX_MESSAGES_PER_RUN threads; return count sent."""
    capped = threads[: config.MAX_MESSAGES_PER_RUN]
    sent = 0

    if not capped:
        return 0

    send_digest_header(len(capped))
    time.sleep(1)

    for thread in capped:
        msg = _format_message(thread)
        ok = send_message(msg)
        if ok:
            sent += 1
        # Telegram allows ~30 messages/sec for bots; be conservative
        time.sleep(0.5)

    log.info("Sent %d/%d messages to Telegram", sent, len(capped))
    return sent
