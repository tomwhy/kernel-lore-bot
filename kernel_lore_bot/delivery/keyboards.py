"""Inline follow/unfollow buttons and their callback data."""

from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CB_FOLLOW = "follow:"
CB_UNFOLLOW = "unfollow:"


def follow_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔔 Follow", callback_data=f"{CB_FOLLOW}{thread_id}")]]
    )


def unfollow_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔕 Unfollow", callback_data=f"{CB_UNFOLLOW}{thread_id}")]]
    )


def parse_callback(data: object) -> Optional[tuple[str, str]]:
    """
    Decode button callback data into (action, thread_id).

    Returns None for anything unrecognised. Note `data` is typed `object`: after
    a restart python-telegram-bot hands back an InvalidCallbackData instance
    rather than a string, and that must not raise.
    """
    if not isinstance(data, str):
        return None
    if data.startswith(CB_FOLLOW):
        return ("follow", data[len(CB_FOLLOW):])
    if data.startswith(CB_UNFOLLOW):
        return ("unfollow", data[len(CB_UNFOLLOW):])
    return None
