from kernel_lore_bot.sources.lore import mbox


def _single_entry(mbox_text):
    entries = [
        e for e in map(mbox.parse_message, mbox.iter_messages(mbox_text)) if e is not None
    ]
    assert len(entries) == 1
    return entries[0]


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


# DEFECT 20: the From header's display name must be RFC 2047-decoded, the
# same way the Subject already is via decode_header_value. parseaddr alone
# does not decode encoded-words, so an encoded author reached users as
# mojibake like "=?utf-8?q?Bj=C3=B6rn_Andersson?=".


def test_parse_message_decodes_rfc2047_author_display_name():
    text = (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: =?utf-8?q?Bj=C3=B6rn_Andersson?= <bjorn@example.com>\n"
        "Subject: test\n"
        "Message-Id: <a@example.com>\n"
        "Date: Mon, 15 Jun 2026 13:00:00 +0000\n"
        "\n"
        "body\n"
    )
    assert _single_entry(text).author == "Björn Andersson"


def test_parse_message_leaves_plain_ascii_author_unchanged():
    text = (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: Dave Davis <dave@example.com>\n"
        "Subject: test\n"
        "Message-Id: <b@example.com>\n"
        "Date: Mon, 15 Jun 2026 13:00:00 +0000\n"
        "\n"
        "body\n"
    )
    assert _single_entry(text).author == "Dave Davis"


def test_parse_message_falls_back_to_bare_address_when_no_display_name():
    text = (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: nodisplayname@example.com\n"
        "Subject: test\n"
        "Message-Id: <c@example.com>\n"
        "Date: Mon, 15 Jun 2026 13:00:00 +0000\n"
        "\n"
        "body\n"
    )
    assert _single_entry(text).author == "nodisplayname@example.com"


def test_parse_message_decodes_author_safely_when_decoded_text_looks_like_an_address():
    # If the raw header were decoded *before* splitting, this decoded display
    # name ("Evil <admin@example.com>") would itself contain '<' and '>' and
    # would confuse parseaddr into extracting the wrong "address" (or none at
    # all) instead of the real one. Splitting first (on the still-encoded raw
    # header) and decoding only the extracted display-name part sidesteps
    # this entirely, because parseaddr never sees the confusing characters.
    text = (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: =?utf-8?b?RXZpbCA8YWRtaW5AZXhhbXBsZS5jb20+?= <real@example.com>\n"
        "Subject: test\n"
        "Message-Id: <d@example.com>\n"
        "Date: Mon, 15 Jun 2026 13:00:00 +0000\n"
        "\n"
        "body\n"
    )
    entry = _single_entry(text)
    assert entry.author == "Evil <admin@example.com>"


def test_real_malformed_fixture_author_is_decoded(fixture_text):
    # tests/fixtures/lore/thread_malformed.mbox has an RFC 2047-encoded From
    # header. No existing test previously asserted its (buggy, encoded)
    # author value, so this is a new assertion, not a changed one.
    first = next(
        e
        for e in map(mbox.parse_message, mbox.iter_messages(fixture_text("thread_malformed.mbox")))
        if e is not None and e.id == "malformed-1@example.com"
    )
    assert first.author == "Björn Andersson"
