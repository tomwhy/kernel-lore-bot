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

# -----------------------------------------------------------
# Relevance filtering
# Threads whose subject matches ANY keyword below are kept.
# Case-insensitive substring match.
# Security takes priority over feature when both match.
# -----------------------------------------------------------

# 🔴 Security / CVE — ordered from most to least specific
SECURITY_KEYWORDS = [
    # CVE references (kernel became its own CNA in 2024 — volume is high)
    "CVE-",
    "fix CVE",
    "fixes CVE",

    # Exploit primitive keywords (what actually shows up in subject lines)
    "use-after-free",
    "UAF",
    "heap overflow",
    "stack overflow",
    "buffer overflow",
    "out-of-bounds",
    "OOB write",
    "OOB read",
    "integer overflow",
    "integer underflow",
    "type confusion",
    "double free",
    "null deref",
    "null pointer deref",
    "wild pointer",
    "dangling pointer",

    # Memory safety sanitizers (patches triggered by these are nearly always security-related)
    "KASAN",
    "KMSAN",
    "UBSAN",
    "KCSAN",
    "syzbot",       # automated fuzzer — almost always a bug fix
    "syzkaller",

    # Attack class / impact keywords
    "privilege escal",
    "privesc",
    "local privilege",
    "container escape",
    "sandbox escape",
    "arbitrary code exec",
    "RCE",
    "remote code exec",
    "information leak",
    "info leak",
    "infoleak",
    "kernel leak",
    "memory leak",      # narrower than it looks in kernel context
    "race condition",   # very common kernel bug class
    "TOCTOU",
    "memory corruption",
    "memory safety",

    # Stable/security tree signals
    "security fix",
    "security patch",
    "[stable]",         # backport to stable tree
    "Cc: stable",
    "regression fix",   # regressions in security subsystems matter

    # Subsystem-level signals
    "selinux:",
    "apparmor:",
    "smack:",
    "seccomp:",
    "landlock:",
    "integrity:",
    "ima:",             # Integrity Measurement Architecture
    "keys:",            # kernel keyring vulnerabilities
    "audit:",
]

# 🟢 New features / subsystem work — structured by signal type
FEATURE_KEYWORDS = [
    # Canonical subject prefixes (most reliable signal)
    "[PATCH]",
    "[PATCH v",         # versioned patch: [PATCH v2], [PATCH v3] ...
    "[RFC]",
    "[RFC PATCH]",
    "[RFC v",
    "[GIT PULL]",       # maintainer pull request to Linus
    "[RESEND]",         # resent patches are still new features

    # Patch cover letters (multi-patch series always has one)
    "[PATCH 0/",        # cover letter: [PATCH 0/N]
    "[RFC 0/",

    # Driver / hardware
    "new driver",
    "add driver",
    "add support for",
    "add support of",
    "enable support",
    "initial support",
    "introduce",
    "implement",

    # Subsystem prefixes (kernel convention: "subsystem: description")
    # Networking
    "net:",
    "netdev:",
    "wifi:",
    "bluetooth:",
    "tcp:",
    "udp:",
    "ipv6:",

    # Storage & filesystems
    "fs:",
    "ext4:",
    "btrfs:",
    "xfs:",
    "nfs:",
    "io_uring:",
    "block:",
    "nvme:",
    "scsi:",

    # Memory management
    "mm:",
    "mmap:",
    "vmalloc:",
    "hugetlb:",
    "swap:",

    # Scheduler & CPU
    "sched:",
    "cpufreq:",
    "cpuidle:",
    "thermal:",
    "perf:",

    # Virtualization & containers
    "kvm:",
    "virtio:",
    "vhost:",
    "cgroup:",
    "namespaces:",

    # eBPF / tracing
    "bpf:",
    "xdp:",
    "tracing:",
    "ftrace:",
    "kprobes:",

    # Graphics & display
    "drm:",
    "dma-buf:",
    "fbdev:",

    # Rust in the kernel
    "rust:",

    # Architecture ports
    "arm64:",
    "x86:",
    "riscv:",
    "loongarch:",
    "powerpc:",
    "s390:",
    "mips:",

    # Device / platform
    "dts:",             # device tree source additions
    "dt-bindings:",
    "platform:",
    "acpi:",
    "pci:",
    "usb:",
    "gpio:",
    "i2c:",
    "spi:",
    "iio:",

    # Explicit feature language
    "add new",
    "new feature",
    "new subsystem",
    "new syscall",
    "extend",
    "rework",
    "refactor",         # architectural changes worth tracking
    "convert to",       # e.g. "convert to folio API"
    "wire up",
    "cleanup series",   # large cleanups often precede new features
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
SUBSCRIBERS_FILE = STATE_DIR / "subscribers.json"
