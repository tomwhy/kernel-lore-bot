"""
poller.py – long-poll Telegram for updates and handle bot commands

Commands available to all users:
  /start   – subscribe to the daily digest
  /stop    – unsubscribe
  /status  – show subscription status

Admin-only commands (ADMIN_CHAT_ID in config.py):
  /debug   – trigger an immediate scrape and broadcast right now
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import requests

import config
import subscribers
from notifier import send_to

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30

_stop_event = threading.Event()

# Injected by bot.py at startup so the poller can call run_job()
# without a circular import.
_run_job_fn: Callable[[], None] | None = None

def register_run_job(fn: Callable[[], None]) -> None:
    global _run_job_fn
    _run_job_fn = fn


# ------------------------------------------------------------------ #
#  Static reply strings                                                #
# ------------------------------------------------------------------ #

WELCOME_MSG = (
    "👋 *Welcome to Kernel Lore Bot\\!*\n\n"
    "You'll receive a daily digest of interesting Linux kernel threads:\n"
    "🔴 Security fixes \\& CVEs\n"
    "🟢 New features \\& patches\n\n"
    "Commands:\n"
    "`/start`  — subscribe to the daily digest\n"
    "`/stop`   — unsubscribe\n"
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

DEBUG_START_MSG   = "🔧 *Debug triggered* — scraping feeds now\\.\\.\\."
DEBUG_NO_ADMIN    = "⛔ This command is restricted to the bot administrator\\."
DEBUG_NOT_SET     = "⚠️ `ADMIN_CHAT_ID` is not configured\\. Set it in config\\.py or via the env var to enable debug commands\\."


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _reply(chat_id: int, text: str) -> None:
    send_to(chat_id, text)


def _is_admin(chat_id: int) -> bool:
    return config.ADMIN_CHAT_ID != 0 and chat_id == config.ADMIN_CHAT_ID


def _get_updates(offset: int | None) -> list[dict]:
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates")
    try:
        resp = requests.get(url, params=params, timeout=POLL_TIMEOUT + 10)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except requests.RequestException as exc:
        log.warning("getUpdates error: %s", exc)
        return []


# ------------------------------------------------------------------ #
#  Command dispatcher                                                  #
# ------------------------------------------------------------------ #

def _handle_message(message: dict) -> None:
    chat_id:    int = message["chat"]["id"]
    text:       str = message.get("text", "").strip()
    first_name: str = message.get("from", {}).get("first_name", "someone")
    username:   str = message.get("from", {}).get("username", "")

    log.debug("Message from %s (chat=%d): %r", username or first_name, chat_id, text)

    cmd = text.split()[0].lower().split("@")[0] if text else ""

    if cmd == "/start":
        is_new = subscribers.add(chat_id)
        _reply(chat_id, WELCOME_MSG if is_new else ALREADY_SUBSCRIBED_MSG)
        if is_new:
            log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)

    elif cmd == "/stop":
        was_subbed = subscribers.remove(chat_id)
        _reply(chat_id, GOODBYE_MSG if was_subbed else NOT_SUBSCRIBED_MSG)
        log.info("/stop from %s (chat=%d)", first_name, chat_id)

    elif cmd == "/status":
        in_subs = chat_id in subscribers.load()
        _reply(chat_id, STATUS_ON_MSG if in_subs else STATUS_OFF_MSG)

    elif cmd == "/debug":
        _handle_debug(chat_id, first_name)

    else:
        if chat_id not in subscribers.load():
            _reply(chat_id, "Send /start to subscribe to the daily kernel digest\\.")


def _handle_debug(chat_id: int, first_name: str) -> None:
    """Trigger an immediate scrape + broadcast. Admin-only."""
    if config.ADMIN_CHAT_ID == 0:
        _reply(chat_id, DEBUG_NOT_SET)
        log.warning("/debug from %s (chat=%d) — ADMIN_CHAT_ID not configured", first_name, chat_id)
        return

    if not _is_admin(chat_id):
        _reply(chat_id, DEBUG_NO_ADMIN)
        log.warning("/debug rejected for %s (chat=%d) — not admin", first_name, chat_id)
        return

    log.info("/debug triggered by %s (chat=%d)", first_name, chat_id)
    _reply(chat_id, DEBUG_START_MSG)

    if _run_job_fn is None:
        _reply(chat_id, "⚠️ run\\_job not registered — this is a bug\\.")
        return

    try:
        _run_job_fn()
        _reply(chat_id, "✅ Debug run complete\\.")
    except Exception as exc:  # noqa: BLE001
        log.exception("Error during debug run_job: %s", exc)
        _reply(chat_id, f"❌ Run failed: {str(exc)[:200]}")


# ------------------------------------------------------------------ #
#  Polling loop                                                        #
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
            time.sleep(1)

    log.info("Telegram poller stopped.")


def start() -> threading.Thread:
    t = threading.Thread(target=_poll_loop, name="tg-poller", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop_event.set()
