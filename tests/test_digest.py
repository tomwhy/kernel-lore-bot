from datetime import datetime, timedelta, timezone

from kernel_lore_bot.digest import classify, count_entries_since
from kernel_lore_bot.models import Entry, Node, Thread, ThreadStatus

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _entry(msg_id: str, updated: datetime) -> Entry:
    return Entry(
        id=msg_id, title=msg_id, url="u", author="a", updated=updated, reply=None
    )


def _thread(msg_id: str, updated: datetime, children=()) -> Thread:
    return Thread(roots=(Node(entry=_entry(msg_id, updated), children=children),))


def test_thread_at_the_cutoff_is_new():
    result = classify([_thread("a", CUTOFF)], CUTOFF)
    assert result[0].status is ThreadStatus.NEW


def test_thread_one_second_before_the_cutoff_is_updated():
    result = classify([_thread("a", CUTOFF - timedelta(seconds=1))], CUTOFF)
    assert result[0].status is ThreadStatus.UPDATED


def test_new_threads_sort_before_updated_threads():
    threads = [
        _thread("old", CUTOFF - timedelta(hours=1)),
        _thread("new", CUTOFF + timedelta(hours=1)),
    ]
    assert [c.thread.id for c in classify(threads, CUTOFF)] == ["new", "old"]


def test_newest_first_within_each_group():
    threads = [
        _thread("newer", CUTOFF + timedelta(hours=2)),
        _thread("newest", CUTOFF + timedelta(hours=3)),
        _thread("older", CUTOFF - timedelta(hours=3)),
        _thread("oldest", CUTOFF - timedelta(hours=4)),
    ]
    assert [c.thread.id for c in classify(threads, CUTOFF)] == [
        "newest",
        "newer",
        "older",
        "oldest",
    ]


def test_classify_of_nothing_is_nothing():
    assert classify([], CUTOFF) == []


def test_classify_does_not_mutate_the_input_threads():
    thread = _thread("a", CUTOFF)
    classify([thread], CUTOFF)
    assert not hasattr(thread, "status")


def test_count_entries_since_counts_the_whole_subtree():
    leaf = Node(entry=_entry("leaf", CUTOFF + timedelta(hours=1)))
    mid = Node(entry=_entry("mid", CUTOFF - timedelta(hours=5)), children=(leaf,))
    thread = _thread("root", CUTOFF + timedelta(hours=2), children=(mid,))
    # root and leaf are within the cutoff; mid is not.
    assert count_entries_since(thread, CUTOFF) == 2


def test_count_entries_since_counts_across_multiple_roots():
    thread = Thread(
        roots=(
            Node(entry=_entry("a", CUTOFF + timedelta(hours=1))),
            Node(entry=_entry("b", CUTOFF + timedelta(hours=2))),
        )
    )
    assert count_entries_since(thread, CUTOFF) == 2


def test_count_entries_since_can_be_zero():
    thread = _thread("root", CUTOFF - timedelta(days=1))
    assert count_entries_since(thread, CUTOFF) == 0
