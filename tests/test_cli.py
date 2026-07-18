from datetime import datetime, timedelta, timezone

from kernel_lore_bot import cli
from kernel_lore_bot.models import Classified, Entry, Node, Thread, ThreadStatus
from kernel_lore_bot.settings import PLACEHOLDER_TOKEN, Settings

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

def test_build_components_returns_a_store_source_and_filters(tmp_path):
    settings = Settings(
        state_dir=tmp_path, mailing_lists=("netdev",), blocked_authors=("robot",)
    )
    store, source, filters = cli.build_components(settings)
    assert store.subscribers() == set()
    assert source.mailing_lists == ("netdev",)
    assert len(filters) == 1
    assert filters[0].names == ("robot",)


def test_build_components_makes_no_filters_when_none_configured(tmp_path):
    settings = Settings(state_dir=tmp_path, blocked_authors=())
    _, _, filters = cli.build_components(settings)
    assert filters == []
