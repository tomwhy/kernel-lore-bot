from kernel_lore_bot.sources.lore import mbox


def test_iter_messages_yields_each_message(fixture_text):
    assert len(list(mbox.iter_messages(fixture_text("thread_mt6392.mbox")))) == 11


def test_iter_messages_on_empty_input_yields_nothing():
    # Regression: this used to raise RuntimeError (PEP 479) and abort the scrape.
    assert list(mbox.iter_messages("")) == []


def test_iter_messages_on_garbage_without_separator_yields_nothing():
    assert list(mbox.iter_messages("<html>503 Service Unavailable</html>")) == []


def test_parse_thread_builds_a_single_root_tree(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"), "linux-input")
    assert len(thread.roots) == 1
    assert thread.id == "20260621081634.467858-1-l.scorcia@gmail.com"
    assert thread.mailing_list == "linux-input"


def test_parse_thread_nests_replies_under_their_parent(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"))
    root = thread.roots[0]
    # Most replies in this thread reply directly to the root; one reply is
    # nested one level deeper (a reply to a reply), making this a genuine
    # 3-level tree rather than a flat root-plus-children shape.
    assert len(root.children) == 9
    assert len(list(thread.walk())) == 11


def test_parse_thread_children_are_sorted_by_date(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"))
    dates = [c.entry.updated for c in thread.roots[0].children]
    assert dates == sorted(dates)


def test_parse_thread_keeps_multiple_roots(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_multi_root.mbox"))
    assert {r.entry.id for r in thread.roots} == {"root-a@example.com", "root-b@example.com"}


def test_parse_thread_falls_back_to_first_message_when_no_root(fixture_text):
    # Every message is a reply to something outside the mbox.
    thread = mbox.parse_thread(fixture_text("thread_orphan_reply.mbox"))
    assert len(thread.roots) == 1
    assert thread.roots[0].entry.id == "orphan-1@example.com"


def test_parse_thread_returns_none_for_empty_input():
    assert mbox.parse_thread("") is None


def test_parse_thread_returns_none_when_nothing_parses(fixture_text):
    # A message with no Message-ID is dropped; if that leaves nothing, no thread.
    assert mbox.parse_thread("From mboxrd@z Thu Jan  1 00:00:00 1970\nSubject: x\n\nbody\n") is None


def test_build_thread_promotes_orphan_replies_to_roots():
    # DEFECT 9 fix: the old code kept a reply only if its parent was present in
    # the same mbox, so a reply pointing outside vanished from the tree entirely
    # and was silently missing from the "N new entries" count.
    from datetime import datetime, timezone

    from kernel_lore_bot.models import Entry, Reply

    def entry(msg_id, ref=None):
        return Entry(
            id=msg_id,
            title=msg_id,
            url="u",
            author="a",
            updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reply=Reply(ref=ref) if ref else None,
        )

    thread = mbox.build_thread([entry("root"), entry("stray", ref="not-here")])
    assert {r.entry.id for r in thread.roots} == {"root", "stray"}
    # Both entries appear somewhere in the tree; this fixture happens to put
    # each in its own root, but that is not guaranteed in general (see
    # DEFECT 15 below for shapes where an orphaned reply is nested instead).
    assert len(list(thread.walk())) == 2


def test_build_thread_returns_none_for_no_entries():
    assert mbox.build_thread([]) is None


def test_build_thread_reaches_disconnected_cycle_alongside_a_real_root():
    # DEFECT 15 fix: the old root rule only checked whether an entry's own
    # In-Reply-To resolved *outside* the mbox. A disconnected pair of entries
    # that reply to each other (refs resolve, but only within the pair, and
    # never back to the real root) was neither a root nor reachable from one,
    # so it vanished from the tree silently.
    from datetime import datetime, timezone

    from kernel_lore_bot.models import Entry, Reply

    def entry(msg_id, ref=None):
        return Entry(
            id=msg_id,
            title=msg_id,
            url="u",
            author="a",
            updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reply=Reply(ref=ref) if ref else None,
        )

    thread = mbox.build_thread([entry("A"), entry("D", ref="E"), entry("E", ref="D")])
    assert len(list(thread.walk())) == 3
    ids = {n.entry.id for n in thread.walk()}
    assert ids == {"A", "D", "E"}


def test_build_thread_on_pure_cycle_does_not_recurse_forever():
    # DEFECT 15 fix: when every entry is part of a reference cycle, there is
    # no real root at all. The old code fell back to `entries[0]` and then
    # recursed into the cycle without ever stopping, raising RecursionError.
    from datetime import datetime, timezone

    from kernel_lore_bot.models import Entry, Reply

    def entry(msg_id, ref=None):
        return Entry(
            id=msg_id,
            title=msg_id,
            url="u",
            author="a",
            updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reply=Reply(ref=ref) if ref else None,
        )

    thread = mbox.build_thread([entry("D", ref="E"), entry("E", ref="D")])
    assert thread is not None
    ids = {n.entry.id for n in thread.walk()}
    assert ids == {"D", "E"}
