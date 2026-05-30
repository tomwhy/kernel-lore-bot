"""
notifier.py – format and broadcast Telegram messages to all subscribers

Threads are packed into batched messages (≤ TELEGRAM_MAX_CHARS each),
split on thread boundaries so no single thread is ever cut in half.
"""

from __future__ import annotations

import logging
import time

import requests

import config
import subscribers
from scraper import Thread

from typing import Generator

log = logging.getLogger(__name__)

TELEGRAM_API       = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_CHARS = 4096   # Telegram hard limit per message
BATCH_SAFE_LIMIT   = 3900   # Leave headroom for the header on the first chunk

LABEL_ICON = {"security": "🔴", "feature": "🟢"}
LABEL_TAG  = {"security": "#security #CVE", "feature": "#feature #kernel"}


# ------------------------------------------------------------------ #
#  Formatting                                                          #
# ------------------------------------------------------------------ #

def _escape(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _format_thread(thread: Thread) -> str:
    """Render one thread as a MarkdownV2 block."""
    icon     = LABEL_ICON.get(thread.label, "⚪")
    tags     = LABEL_TAG.get(thread.label, "")
    date_str = thread.updated.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"{icon} *{_escape(thread.title)}*",
        f"📋 `{_escape(thread.list_name)}`  👤 {_escape(thread.author)}  🕐 {_escape(date_str)}",
    ]
    if thread.summary:
        snip = thread.summary[:160].replace("\n", " ")
        lines.append(f"_{_escape(snip)}_")
    lines.append(f"[🔗 View thread]({thread.url})  {tags}")

    return "\n".join(lines)


def _format_header(total: int) -> str:
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return (
        f"🐧 *Kernel Lore Daily Digest*\n"
        f"_{_escape(now)}_\n\n"
        f"Found *{count}* interesting thread(s) today\\.\n\n"
    )


# ------------------------------------------------------------------ #
#  Batching                                                            #
# ------------------------------------------------------------------ #

THREAD_SEPARATOR = "\n\n" + "─" * 30 + "\n\n"


def _build_batches(threads: list[Thread]) -> Generator[str, None, None]:
    """
    Pack threads into as few messages as possible, splitting only on
    thread boundaries so no thread is ever truncated.
    Each batch is guaranteed to be ≤ TELEGRAM_MAX_CHARS characters.
    """

    msg: str = _format_header(len(threads))
    current: str = None
    need_sep = False

    if not threads:
        return

    for thead in threads:
        current = _format_thread(thread)
        needed = len(current)
        if need_sep:
            need_sep += len(THREAD_SEPARATOR)

        if len(msg) + needed > TELEGRAM_MAX_CHARS:
            yield msg
            msg = current
        else:
            msg += current
            if need_sep:
                msg += THREAD_SEPARATOR
            need_sep = True

    yield msg


# ------------------------------------------------------------------ #
#  Low-level send                                                      #
# ------------------------------------------------------------------ #

def send_to(chat_id: int, text: str) -> bool:
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id":                chat_id,
        "text":                   text,
        "parse_mode":             "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Telegram send failed (chat=%d): %s", chat_id, exc)
        return False


def _broadcast_batch(text: str, chat_ids: set[int]) -> tuple[int, list[int]]:
    """Send one message to every subscriber. Returns (success_count, blocked_ids)."""
    sent    = 0
    blocked = []
    for chat_id in chat_ids:
        if send_to(chat_id, text):
            sent += 1
        else:
            blocked.append(chat_id)
        time.sleep(0.05)
    return sent, blocked


# ------------------------------------------------------------------ #
#  Public API                                                          #
# ------------------------------------------------------------------ #

def send_threads(threads: list[Thread]) -> int:
    """
    Broadcast all new threads to every subscriber, batched into the
    minimum number of Telegram messages required.
    Returns total individual sends (batches × subscribers).
    """
    if not threads:
        log.info("No new threads to send.")
        return 0

    chat_ids = subscribers.load()
    if not chat_ids:
        log.info("No subscribers yet — nothing to send.")
        return 0

    batches = _build_batches(threads)
    log.info(
        "Broadcasting %d thread(s) to %d subscriber(s)",
        len(threads), len(chat_ids)
    )

    total_sent   = 0
    all_blocked: set[int] = set()

    for i, batch_text in enumerate(batches, start=1):
        sent, blocked = _broadcast_batch(batch_text, chat_ids)
        total_sent += sent
        all_blocked.update(blocked)
        log.debug("Batch #%d — sent to %d/%d", i, len(batches), sent, len(chat_ids))
        time.sleep(0.1) 

    if all_blocked:
        subscribers.remove_many(all_blocked)
        log.info("Auto-removed %d blocked/unreachable subscriber(s)", len(all_blocked))

    log.info("Broadcast complete — %d total send(s)", total_sent)
    return total_sent
