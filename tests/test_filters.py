from datetime import datetime, timezone

from kernel_lore_bot.filters import BlockedAuthors, apply_filters
from kernel_lore_bot.models import Entry, Node, Thread


def _thread(author: str) -> Thread:
    entry = Entry(
        id=f"{author}@x.com",
        title="t",
        url="u",
        author=author,
        updated=datetime(2026, 7, 16, tzinfo=timezone.utc),
        reply=None,
    )
    return Thread(roots=(Node(entry=entry),))


def test_blocked_authors_matches_case_insensitively():
    f = BlockedAuthors(("kernel test robot",))
    assert f.allows(_thread("Kernel Test Robot")) is False
    assert f.allows(_thread("Linus Torvalds")) is True


def test_blocked_authors_matches_a_substring():
    f = BlockedAuthors(("robot",))
    assert f.allows(_thread("kernel test robot")) is False


def test_blocked_authors_with_no_names_allows_everything():
    assert BlockedAuthors(()).allows(_thread("anyone")) is True


def test_apply_filters_drops_only_rejected_threads():
    threads = [_thread("kernel test robot"), _thread("Linus Torvalds")]
    kept = apply_filters(threads, [BlockedAuthors(("kernel test robot",))])
    assert [t.author for t in kept] == ["Linus Torvalds"]


def test_apply_filters_requires_every_filter_to_allow():
    class RejectAll:
        def allows(self, thread):
            return False

    kept = apply_filters([_thread("Linus Torvalds")], [BlockedAuthors(()), RejectAll()])
    assert kept == []


def test_apply_filters_with_no_filters_keeps_everything():
    threads = [_thread("a"), _thread("b")]
    assert apply_filters(threads, []) == threads
