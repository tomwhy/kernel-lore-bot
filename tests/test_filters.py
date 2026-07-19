from datetime import datetime, timezone

from kernel_lore_bot.filters import BlockedAuthors, apply_filters
from kernel_lore_bot.models import Entry, Node, Thread


def _thread(author: str, author_email: str = "") -> Thread:
    entry = Entry(
        id=f"{author}@x.com",
        title="t",
        url="u",
        author=author,
        author_email=author_email,
        updated=datetime(2026, 7, 16, tzinfo=timezone.utc),
        reply=None,
    )
    return Thread(roots=(Node(entry=entry),))


def test_blocked_authors_matches_the_whole_address():
    f = BlockedAuthors(("lkp@intel.com",))
    assert f.allows(_thread("Kernel Test Robot", "lkp@intel.com")) is False
    assert f.allows(_thread("Linus Torvalds", "torvalds@linux-foundation.org")) is True


def test_blocked_authors_ignores_the_display_name():
    """The name is no longer consulted at all — only the address decides."""
    f = BlockedAuthors(("kernel test robot",))
    assert f.allows(_thread("kernel test robot", "lkp@intel.com")) is True


def test_blocked_authors_does_not_match_a_substring():
    """A blocked address must not mute every address containing it."""
    f = BlockedAuthors(("lkp@intel.com",))
    assert f.allows(_thread("Someone", "not-lkp@intel.com.example.org")) is True
    assert f.allows(_thread("Someone", "xlkp@intel.com")) is True


def test_blocked_authors_matches_case_insensitively():
    """Addresses differing only in case are the same mailbox in practice."""
    f = BlockedAuthors(("LKP@Intel.COM",))
    assert f.allows(_thread("Robot", "lkp@intel.com")) is False


def test_blocked_authors_ignores_surrounding_whitespace():
    f = BlockedAuthors(("  lkp@intel.com  ",))
    assert f.allows(_thread("Robot", "lkp@intel.com")) is False


def test_thread_with_no_address_is_never_blocked():
    """An unparseable From: must not be silenced by an unrelated block."""
    f = BlockedAuthors(("lkp@intel.com",))
    assert f.allows(_thread("Unknown", "")) is True


def test_empty_block_entry_does_not_mute_addressless_threads():
    """A stray "" in the blocklist must not match every addressless thread."""
    f = BlockedAuthors(("",))
    assert f.allows(_thread("Unknown", "")) is True


def test_blocked_authors_with_no_addresses_allows_everything():
    assert BlockedAuthors(()).allows(_thread("anyone", "anyone@example.com")) is True


def test_apply_filters_drops_only_rejected_threads():
    threads = [
        _thread("Kernel Test Robot", "lkp@intel.com"),
        _thread("Linus Torvalds", "torvalds@linux-foundation.org"),
    ]
    kept = apply_filters(threads, [BlockedAuthors(("lkp@intel.com",))])
    assert [t.author for t in kept] == ["Linus Torvalds"]


def test_apply_filters_requires_every_filter_to_allow():
    class RejectAll:
        def allows(self, thread):
            return False

    kept = apply_filters([_thread("Linus Torvalds", "t@x.org")], [BlockedAuthors(()), RejectAll()])
    assert kept == []


def test_apply_filters_with_no_filters_keeps_everything():
    threads = [_thread("a", "a@x.org"), _thread("b", "b@x.org")]
    assert apply_filters(threads, []) == threads
