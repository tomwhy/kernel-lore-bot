from datetime import datetime, timezone

from kernel_lore_bot.models import (
    Classified,
    Entry,
    Node,
    Reply,
    Thread,
    ThreadStatus,
)


def _entry(msg_id: str, reply_to: str | None = None) -> Entry:
    return Entry(
        id=msg_id,
        title=f"subject {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author="Someone",
        updated=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        reply=Reply(ref=reply_to) if reply_to else None,
    )


def test_entry_is_reply_reflects_reply_field():
    assert _entry("a").is_reply is False
    assert _entry("b", reply_to="a").is_reply is True


def test_entry_equality_and_hash_use_id_only():
    left = _entry("same")
    right = Entry(
        id="same",
        title="totally different",
        url="different",
        author="different",
        updated=datetime(1999, 1, 1, tzinfo=timezone.utc),
        reply=None,
    )
    assert left == right
    assert len({left, right}) == 1


def test_entry_not_equal_to_other_types():
    assert _entry("a") != "a"


def test_node_walk_yields_self_then_descendants():
    leaf = Node(entry=_entry("leaf", reply_to="mid"))
    mid = Node(entry=_entry("mid", reply_to="root"), children=(leaf,))
    root = Node(entry=_entry("root"), children=(mid,))
    assert [n.entry.id for n in root.walk()] == ["root", "mid", "leaf"]


def test_thread_properties_delegate_to_first_root():
    root = Node(entry=_entry("root"))
    thread = Thread(roots=(root,), mailing_list="netdev")
    assert thread.id == "root"
    assert thread.title == "subject root"
    assert thread.author == "Someone"
    assert thread.url == "https://lore.kernel.org/all/root"
    assert thread.updated == datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    assert thread.mailing_list == "netdev"


def test_thread_walk_covers_every_root():
    a = Node(entry=_entry("a"))
    b = Node(entry=_entry("b"), children=(Node(entry=_entry("b1", reply_to="b")),))
    thread = Thread(roots=(a, b))
    assert sorted(n.entry.id for n in thread.walk()) == ["a", "b", "b1"]


def test_thread_status_values_match_wire_strings():
    assert ThreadStatus.NEW.value == "new"
    assert ThreadStatus.UPDATED.value == "updated"


def test_classified_pairs_thread_with_status():
    thread = Thread(roots=(Node(entry=_entry("root")),))
    c = Classified(thread=thread, status=ThreadStatus.NEW)
    assert c.thread is thread
    assert c.status is ThreadStatus.NEW
