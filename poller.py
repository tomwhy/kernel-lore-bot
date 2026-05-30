"""
poller.py – long-poll Telegram for updates and handle /start / /stop commands

Runs in a background thread alongside the daily scheduler.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

import config
import subscribers
from notifier import send_to

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# How long to wait for new updates from Telegram (seconds).
# Telegram holds the connection open until a message arrives or timeout fires.
POLL_TIMEOUT = 30

_stop_event = threading.Event()


# ------------------------------------------------------------------ #
#  Bot command responses                                               #
# ------------------------------------------------------------------ #

WELCOME_MSG = (
    "👋 *Welcome to Kernel Lore Bot\\!*\n\n"
    "You'll receive a daily digest of interesting Linux kernel threads:\n"
    "🔴 Security fixes \\& CVEs\n"
    "🟢 New features \\& patches\n\n"
    "Commands:\n"
    "`/start` — subscribe to the daily digest\n"
    "`/stop`  — unsubscribe\n"
    "`/status` — check your subscription status\n"
)

ALREADY_SUBSCRIBED_MSG = (
    "✅ You're already subscribed\\! "
    "You'll receive the next digest at the scheduled time\\."
)

GOODBYE_MSG = (
    "👋 You've been *unsubscribed*\\.\n"
    "Send /start any time to re\\-subscribe\\."
)

NOT_SUBSCRIBED_MSG = "ℹ️ You weren't subscribed\\."

STATUS_ON_MSG  = "✅ You are *subscribed* to the daily kernel digest\\."
STATUS_OFF_MSG = "❌ You are *not subscribed*\\. Send /start to subscribe\\."


# ------------------------------------------------------------------ #
#  Telegram API helpers                                                #
# ------------------------------------------------------------------ #

def _api(method: str, **kwargs) -> dict | None:
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, json=kwargs, timeout=POLL_TIMEOUT + 5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.warning("Telegram API error (%s): %s", method, exc)
        return None


def _get_updates(offset: int | None) -> list[dict]:
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates")
    try:
        resp = requests.get(url, params=params, timeout=POLL_TIMEOUT + 10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])
    except requests.RequestException as exc:
        log.warning("getUpdates error: %s", exc)
        return []


def _reply(chat_id: int, text: str) -> None:
    send_to(chat_id, text)


# ------------------------------------------------------------------ #
#  Command dispatcher                                                  #
# ------------------------------------------------------------------ #

def _handle_message(message: dict) -> None:
    chat_id: int = message["chat"]["id"]
    text: str = message.get("text", "").strip()
    username: str = message.get("from", {}).get("username", "")
    first_name: str = message.get("from", {}).get("first_name", "someone")

    log.debug("Message from %s (chat=%d): %r", username or first_name, chat_id, text)

    cmd = text.split()[0].lower().split("@")[0] if text else ""

    if cmd == "/start":
        is_new = subscribers.add(chat_id)
        if is_new:
            _reply(chat_id, WELCOME_MSG)
            log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)
        else:
            _reply(chat_id, ALREADY_SUBSCRIBED_MSG)

    elif cmd == "/stop":
        was_subbed = subscribers.remove(chat_id)
        _reply(chat_id, GOODBYE_MSG if was_subbed else NOT_SUBSCRIBED_MSG)
        log.info("/stop from %s (chat=%d)", first_name, chat_id)

    elif cmd == "/status":
        subs = subscribers.load()
        msg = STATUS_ON_MSG if chat_id in subs else STATUS_OFF_MSG
        _reply(chat_id, msg)

    else:
        # Unknown message — nudge them toward /start
        if not subscribers.load().__contains__(chat_id):
            _reply(chat_id, "Send /start to subscribe to the daily kernel digest\\.")


# ------------------------------------------------------------------ #
#  Polling loop (runs in a daemon thread)                              #
# ------------------------------------------------------------------ #

def _poll_loop() -> None:
    log.info("Telegram poller started (long-poll timeout=%ds)", POLL_TIMEOUT)
    offset: int | None = None

    while not _stop_event.is_set():
        updates = _get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message")
            if message:
                try:
                    _handle_message(message)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Error handling message: %s", exc)

        if not updates and not _stop_event.is_set():
            # Back-off briefly on empty poll to avoid hammering on errors
            time.sleep(1)

    log.info("Telegram poller stopped.")


def start() -> threading.Thread:
    """Start the poller in a background daemon thread and return it."""
    t = threading.Thread(target=_poll_loop, name="tg-poller", daemon=True)
    t.start()
    return t


def stop() -> None:
    """Signal the poller to exit on its next iteration."""
    _stop_event.set()
