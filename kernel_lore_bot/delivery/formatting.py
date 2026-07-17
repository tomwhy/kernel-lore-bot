"""
Telegram message text. Pure; no I/O and no clock access.

Everything interpolated here comes from an email header, so every field is
HTML-escaped: Telegram renders these messages with parse_mode=HTML.
"""

from __future__ import annotations

import html
from datetime import datetime

from kernel_lore_bot.digest import count_entries_since
from kernel_lore_bot.models import Classified, Thread, ThreadStatus

STATUS_BADGE: dict[ThreadStatus, str] = {
    ThreadStatus.NEW: "🆕",
    ThreadStatus.UPDATED: "🔄",
}

_DATE_FMT = "%Y-%m-%d %H:%M UTC"


def _h(text: str) -> str:
    return html.escape(text)


def _link(url: str) -> str:
    # quote=True matters: the URL carries an untrusted Message-ID.
    return f'<a href="{html.escape(url, quote=True)}">🔗 View thread</a>'


def format_thread(classified: Classified, cutoff: datetime) -> str:
    """The per-thread digest message."""
    thread = classified.thread
    lines = [
        f"{STATUS_BADGE.get(classified.status, '')} <b>{_h(thread.title)}</b>",
        f"👤 {_h(thread.author)}  🕐 {_h(thread.updated.strftime(_DATE_FMT))}",
    ]

    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")

    new_count = count_entries_since(thread, cutoff)
    if new_count:
        noun = "entry" if new_count == 1 else "entries"
        lines.append(f"<i>... {new_count} new {noun}</i>")

    lines.append(_link(thread.url))
    return "\n".join(lines)


def format_update_notification(thread: Thread) -> str:
    """The message a follower gets when a thread they follow moves."""
    lines = [
        "🔔 <b>Thread update</b>",
        f"<b>{_h(thread.title)}</b>",
        f"👤 {_h(thread.author)}  🕐 {_h(thread.updated.strftime(_DATE_FMT))}",
    ]
    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")
    lines.append(_link(thread.url))
    return "\n".join(lines)


def format_header(total: int, now: datetime) -> str:
    """The digest header sent before the per-thread messages."""
    return (
        f"🐧 <b>Kernel Lore Digest</b>\n"
        f"<i>{now.strftime(_DATE_FMT)}</i> — <b>{total}</b> new thread(s)"
    )
