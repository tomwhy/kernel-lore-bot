from datetime import datetime, timedelta, timezone

from kernel_lore_bot import cli
from kernel_lore_bot.models import Classified, Entry, Node, Thread, ThreadStatus
from kernel_lore_bot.settings import PLACEHOLDER_TOKEN, Settings
from kernel_lore_bot.storage import InMemoryStore

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _classified(msg_id, status, updated=None):
    entry = Entry(
        id=msg_id,
        title=f"[PATCH] {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author="Alice Adams",
        updated=updated or CUTOFF,
        reply=None,
    )
    thread = Thread(roots=(Node(entry=entry),), mailing_lists=frozenset({"netdev"}))
    return Classified(thread=thread, status=status)


# -- config checks --------------------------------------------------

def test_missing_token_is_an_error_in_normal_mode():
    errors = cli.check_config(Settings(telegram_bot_token=PLACEHOLDER_TOKEN), dry=False)
    assert len(errors) == 1
    assert "TELEGRAM_BOT_TOKEN" in errors[0]


def test_missing_token_is_fine_in_dry_mode():
    assert cli.check_config(Settings(telegram_bot_token=PLACEHOLDER_TOKEN), dry=True) == []


def test_a_real_token_passes():
    assert cli.check_config(Settings(telegram_bot_token="123:abc"), dry=False) == []


def test_main_exits_nonzero_when_the_token_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings(state_dir=tmp_path)
    )
    assert cli.main([]) == 1


# -- dry run --------------------------------------------------------

def test_dry_run_reports_nothing_found():
    assert "No new threads" in cli.format_dry_run([], CUTOFF)


def test_dry_run_lists_new_threads_using_the_real_formatter():
    report = cli.format_dry_run([_classified("a@x.com", ThreadStatus.NEW)], CUTOFF)
    assert "1 new thread(s)" in report
    # The same markup the digest would send.
    assert "🆕 <b>[PATCH] a@x.com</b>" in report


def test_dry_run_separates_updated_threads():
    items = [
        _classified("new@x.com", ThreadStatus.NEW),
        _classified("old@x.com", ThreadStatus.UPDATED, CUTOFF - timedelta(days=1)),
    ]
    report = cli.format_dry_run(items, CUTOFF)
    assert "1 updated thread(s)" in report
    assert "🔄 <b>[PATCH] old@x.com</b>" in report


def test_dry_run_with_only_updated_threads_says_zero_new():
    items = [_classified("old@x.com", ThreadStatus.UPDATED, CUTOFF - timedelta(days=1))]
    report = cli.format_dry_run(items, CUTOFF)
    assert "0 new thread(s)" in report


# -- component wiring -----------------------------------------------

def test_build_components_seeds_the_store_from_settings(tmp_path):
    settings = Settings(
        state_dir=tmp_path, mailing_lists=("netdev",), blocked_authors=("robot",)
    )
    store, source, registry = cli.build_components(settings)
    store.add_subscriber(1)

    assert store.mailing_lists(1) == {"netdev"}
    assert store.blocked_authors(1) == {"robot"}


def test_build_components_falls_back_to_the_configured_lists(tmp_path):
    """No network here, so the registry must start on the settings fallback."""
    settings = Settings(state_dir=tmp_path, mailing_lists=("netdev",))
    _, _, registry = cli.build_components(settings)

    assert registry.index.is_valid("netdev") is True


# -- main(["--dry"]) --------------------------------------------------


class _StubSource:
    """No network I/O: fetch_threads just returns canned threads."""

    def __init__(self, threads=()):
        self.threads = list(threads)

    def fetch_threads(self, since, mailing_lists):
        return list(self.threads)

    def fetch_threads_by_id(self, ids):
        return []


def test_main_dry_run_does_not_crash_and_prints_the_report(monkeypatch, tmp_path, capsys):
    """
    Regression test: main(["--dry"]) used to pass the filters list positionally
    into Broadcaster's list_registry parameter, which made collect() call
    .refresh() on a plain list and raise AttributeError before any network
    I/O. build_components is stubbed here so this test never touches the
    network regardless.
    """
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(state_dir=tmp_path))
    monkeypatch.setattr(
        cli,
        "build_components",
        lambda settings: (InMemoryStore(), _StubSource(), []),
    )

    assert cli.main(["--dry"]) == 0
    assert "No new threads" in capsys.readouterr().out
