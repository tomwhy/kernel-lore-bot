# ============================================================
#  Kernel Lore Bot — Configuration
#  Edit this file to customize the bot's behavior
# ============================================================

import os
import pathlib
import datetime
import zoneinfo

# -----------------------------------------------------------
# Telegram
# -----------------------------------------------------------
def _read_secret(name: str, fallback: str = "") -> str:
    secret_path = f"/run/secrets/{name}"
    try:
        with open(secret_path) as _f:
            return _f.read().strip()
    except FileNotFoundError:
        pass
    return os.environ.get(name.upper(), fallback)

TELEGRAM_BOT_TOKEN: str = _read_secret("telegram_bot_token", "YOUR_BOT_TOKEN_HERE")

# Chat ID of the bot administrator.
# Only this user can run privileged commands like /scrape.
# Find yours by messaging @userinfobot on Telegram.
# Leave as 0 to disable privileged commands entirely.
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))

# -----------------------------------------------------------
# Scraping schedule
# -----------------------------------------------------------
# Run every day at 00:00 IST
SCHEDULE_TIME = datetime.time(hour=0, tzinfo=zoneinfo.ZoneInfo("Asia/Jerusalem"))

# How many hours back to look for "new" posts on first run
# (prevents a flood of old messages on initial startup)
LOOPBACK_HOURS = 24

# -----------------------------------------------------------
# Blacklist
# Entries whose title matches any rule below are skipped
# entirely — their thread will not be fetched.
#
# BLACKLIST_TAGS     – exact match (case-insensitive) against bracket tags
#                      e.g. "RFC" blocks [RFC] but not [RFC PATCH]
# BLACKLIST_SUBJECTS – exact match (case-insensitive) against each colon-
#                      delimited subject prefix
#                      e.g. "staging" blocks "staging: ..." but not "drm/staging: ..."
# BLACKLIST_TITLE    – substring match (case-insensitive) against the rest
#                      of the title after tags and subjects are stripped
# -----------------------------------------------------------
BLACKLIST_TAGS: list[str] = [
    "git pull",
    "bluez/bluez",
    "Buildroot",
    # "RFC",
]
 
BLACKLIST_SUBJECTS: list[str] = [
    "RTT-PROBE",
    "qcom",
    "dts",
    "KVM",
    "riscv",
    "e1000",
    "dt-bindings",
    "Documentation",

]
 
BLACKLIST_TITLE: list[str] = [
    # "typo",
    # "Revert",
]

# -----------------------------------------------------------
# Rate limiting / politeness
# -----------------------------------------------------------
REQUEST_DELAY_SECONDS = 0.1     # Pause between HTTP requests
REQUEST_TIMEOUT       = 15      # HTTP timeout per feed

# -----------------------------------------------------------
# State file (tracks subscribers across runs)
# -----------------------------------------------------------
STATE_DIR        = pathlib.Path(os.environ.get("KERNEL_BOT_STATE_DIR", "data"))
SUBSCRIBERS_FILE = STATE_DIR / "subscribers.json"
