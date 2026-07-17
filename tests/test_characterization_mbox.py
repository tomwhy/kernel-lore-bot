"""
Characterization tests: they now cover the extracted kernel_lore_bot.sources.lore.mbox
module. They originally locked in the behavior of the old scraper.py so the
extraction in Task 5 could be proven behavior-preserving; the assertions below
are unchanged from that baseline except for the empty-mbox test, which is
inverted because Task 5 fixes the RuntimeError defect it used to lock in.
"""

from datetime import timezone

from kernel_lore_bot.sources.lore import mbox


def _entries(text):
    return [e for e in map(mbox.parse_message, mbox.iter_messages(text)) if e is not None]


def test_real_thread_parses_every_message(fixture_text):
    assert len(_entries(fixture_text("thread_mt6392.mbox"))) == 11


def test_real_thread_has_exactly_one_root(fixture_text):
    roots = [e for e in _entries(fixture_text("thread_mt6392.mbox")) if not e.is_reply]
    assert len(roots) == 1
    assert roots[0].id == "20260621081634.467858-1-l.scorcia@gmail.com"


def test_real_thread_root_fields(fixture_text):
    root = next(e for e in _entries(fixture_text("thread_mt6392.mbox")) if not e.is_reply)
    assert root.title == "[PATCH v9 0/9] Add support for MT6392 PMIC"
    assert root.author == "Luca Leonardo Scorcia"
    assert root.updated.tzinfo is not None
    assert root.url == (
        "https://lore.kernel.org/all/20260621081634.467858-1-l.scorcia@gmail.com"
    )


def test_mixed_case_message_id_headers_all_parse(fixture_text):
    # lore emits both "Message-ID:" and "Message-Id:" within one thread.
    assert all(e.id for e in _entries(fixture_text("thread_mt6392.mbox")))


def test_single_message_thread(fixture_text):
    entries = _entries(fixture_text("thread_single.mbox"))
    assert len(entries) == 1
    assert entries[0].is_reply is False


def test_multi_root_thread_yields_two_roots(fixture_text):
    roots = [
        e for e in _entries(fixture_text("thread_multi_root.mbox")) if not e.is_reply
    ]
    assert {r.id for r in roots} == {"root-a@example.com", "root-b@example.com"}


def test_orphan_reply_is_parsed_as_a_reply(fixture_text):
    entries = _entries(fixture_text("thread_orphan_reply.mbox"))
    assert len(entries) == 1
    assert entries[0].is_reply is True
    assert entries[0].reply.ref == "missing-parent@example.com"


def test_rfc2047_subject_is_decoded(fixture_text):
    first = next(
        e
        for e in _entries(fixture_text("thread_malformed.mbox"))
        if e.id == "malformed-1@example.com"
    )
    assert first.title == "[PATCH] café support"


def test_unparseable_date_falls_back_to_utc_now(fixture_text):
    first = next(
        e
        for e in _entries(fixture_text("thread_malformed.mbox"))
        if e.id == "malformed-1@example.com"
    )
    assert first.updated.tzinfo == timezone.utc


def test_message_without_message_id_is_dropped(fixture_text):
    entries = _entries(fixture_text("thread_malformed.mbox"))
    assert [e.id for e in entries] == ["malformed-1@example.com"]


def test_empty_mbox_no_longer_raises():
    # Was: RuntimeError from PEP 479, which aborted the entire scrape.
    assert list(mbox.iter_messages("")) == []
