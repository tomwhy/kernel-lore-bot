# ============================================================
#  Kernel Lore Bot — Configuration
#  Edit this file to customize the bot's behavior
# ============================================================

# -----------------------------------------------------------
# Telegram
# -----------------------------------------------------------
# The bot token is loaded from a Docker secret file at runtime
# (/run/secrets/telegram_bot_token) so it is never stored in this
# file or in environment variables.  Fallback order:
#   1. /run/secrets/telegram_bot_token   (Docker secret)
#   2. TELEGRAM_BOT_TOKEN env var        (local dev / non-Docker)
#   3. Hardcoded value below             (last resort / plain runs)
import os 
import pathlib
import datetime
import zoneinfo

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
# Only this user can run privileged commands like /debug.
# Find yours by messaging @userinfobot on Telegram.
# Leave as 0 to disable privileged commands entirely.
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))

# -----------------------------------------------------------
# Scraping schedule
# -----------------------------------------------------------
# run every day at 00:00 IST
SCHEDULE_TIME = datetime.time(hour=0, tzinfo=zoneinfo.ZoneInfo("Asia/Jerusalem"))

# How many hours back to look for "new" posts on first run
# (prevents a flood of old messages on initial startup)
LOOPBACK_HOURS = 24

# -----------------------------------------------------------
# Lore mailing lists to watch
# Each entry: (list_name, atom_url)
# Add/remove lists freely – the bot handles all of them.
# -----------------------------------------------------------
WATCHED_LISTS = [
    ("LKML",            "https://lore.kernel.org/lkml/new.atom"),
    ("netdev",          "https://lore.kernel.org/netdev/new.atom"),
    ("linux-kernel",    "https://lore.kernel.org/linux-kernel/new.atom"),
    ("linux-security",  "https://lore.kernel.org/linux-security-module/new.atom"),
    ("stable",          "https://lore.kernel.org/stable/new.atom"),
    ("linux-mm",        "https://lore.kernel.org/linux-mm/new.atom"),
    ("linux-fsdevel",   "https://lore.kernel.org/linux-fsdevel/new.atom"),
    ("io-uring",        "https://lore.kernel.org/io-uring/new.atom"),
    ("bpf",             "https://lore.kernel.org/bpf/new.atom"),
]

# -----------------------------------------------------------
# Relevance filtering
# Threads whose subject matches ANY keyword below are kept.
# Case-insensitive substring match.
# -----------------------------------------------------------

# Security / CVE keywords → shown with 🔴 label
SECURITY_KEYWORDS = [
    "CVE-",
    "vulnerability",
    "vuln",
    "exploit",
    "RCE",
    "privilege escal",
    "use-after-free",
    "UAF",
    "out-of-bounds",
    "OOB",
    "heap overflow",
    "stack overflow",
    "buffer overflow",
    "race condition",
    "KASAN",
    "UBSAN",
    "null deref",
    "memory corruption",
    "security fix",
    "fix CVE",
]

# New-feature / subsystem keywords → shown with 🟢 label
FEATURE_KEYWORDS = [
    "[PATCH",
    "[RFC",
    "add support",
    "introduce",
    "implement",
    "new driver",
    "new subsystem",
    "enable",
    "feature:",
    "perf:",
    "mm:",
    "net:",
    "fs:",
    "sched:",
    "bpf:",
    "io_uring:",
    "drm:",
    "arm64:",
    "x86:",
    "riscv:",
    "kvm:",
    "virtio:",
    "rust:",
    "[GIT PULL]",
]

# -----------------------------------------------------------
# Rate limiting / politeness
# -----------------------------------------------------------
REQUEST_DELAY_SECONDS  = 0.5    # Pause between HTTP requests
REQUEST_TIMEOUT        = 15   # HTTP timeout per feed

# -----------------------------------------------------------
# State file (tracks seen thread IDs across runs)
# -----------------------------------------------------------
STATE_DIR = pathlib.Path(os.environ.get("KERNEL_BOT_STATE_DIR", "data"))
STATE_FILE = STATE_DIR / "seen_threads.json"
SUBSCRIBERS_FILE = STATE_DIR / "subscribers.json"
