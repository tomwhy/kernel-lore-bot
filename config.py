# ============================================================
#  Kernel Lore Bot — Configuration
#  Edit this file to customize the bot's behavior
# ============================================================

# -----------------------------------------------------------
# Telegram
# -----------------------------------------------------------
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"   # From @BotFather
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID_HERE"     # Channel/group/user ID

# -----------------------------------------------------------
# Scraping schedule
# -----------------------------------------------------------
# Cron-style: run every day at 08:00 UTC
SCHEDULE_CRON = "0 8 * * *"

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
MAX_MESSAGES_PER_RUN   = 30   # Cap to avoid Telegram flood limits
REQUEST_TIMEOUT        = 15   # HTTP timeout per feed

# -----------------------------------------------------------
# State file (tracks seen thread IDs across runs)
# -----------------------------------------------------------
STATE_FILE = "seen_threads.json"
