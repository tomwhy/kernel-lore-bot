import logging
from datetime import datetime, timezone

import pytest

from kernel_lore_bot.sources.lore.atom import FeedParseError, parse_feed_page


def test_parses_every_entry_in_document_order(fixture_bytes):
    entries = parse_feed_page(fixture_bytes("new_page1.atom"))
    assert [e.entry_id for e in entries] == [
        "newest-1@example.com",
        "newest-2@example.com",
    ]


def test_extracts_message_id_from_link_href_path(fixture_bytes):
    first = parse_feed_page(fixture_bytes("new_page1.atom"))[0]
    # href ends in a trailing slash; the id is the last path segment.
    assert first.entry_id == "newest-1@example.com"


def test_parses_zulu_timestamps_as_aware_utc(fixture_bytes):
    first = parse_feed_page(fixture_bytes("new_page1.atom"))[0]
    assert first.updated == datetime(2026, 7, 16, 15, 55, 52, tzinfo=timezone.utc)


def test_empty_feed_yields_no_entries(fixture_bytes):
    assert parse_feed_page(fixture_bytes("new_empty.atom")) == []


def test_malformed_xml_raises_feed_parse_error(fixture_bytes):
    with pytest.raises(FeedParseError):
        parse_feed_page(fixture_bytes("not_xml.atom"))


def test_entry_with_unparseable_date_is_skipped_not_fatal():
    # DEFECT 10: this used to raise an uncaught ValueError and kill the scrape.
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <updated>not-a-date</updated>
        <link href="https://lore.kernel.org/linux-input/bad@example.com/"/>
      </entry>
      <entry>
        <updated>2026-07-16T15:00:00Z</updated>
        <link href="https://lore.kernel.org/linux-input/good@example.com/"/>
      </entry>
    </feed>"""
    assert [e.entry_id for e in parse_feed_page(data)] == ["good@example.com"]


def test_entry_without_link_is_skipped():
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><updated>2026-07-16T15:00:00Z</updated></entry>
    </feed>"""
    assert parse_feed_page(data) == []


# -- finding 6: a page-wide skip must be distinguishable from end-of-pagination --


def test_page_where_every_entry_is_unparseable_logs_a_warning(caplog):
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <updated>not-a-date</updated>
        <link href="https://lore.kernel.org/linux-input/bad1@example.com/"/>
      </entry>
      <entry>
        <updated>also-not-a-date</updated>
        <link href="https://lore.kernel.org/linux-input/bad2@example.com/"/>
      </entry>
    </feed>"""

    with caplog.at_level(logging.WARNING):
        entries = parse_feed_page(data)

    # Return value and control flow are unchanged: still just [].
    assert entries == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a page-wide parse wipeout must log a WARNING"
    assert "2" in warnings[0].getMessage()


def test_page_with_a_mix_of_good_and_bad_entries_does_not_warn(caplog):
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <updated>not-a-date</updated>
        <link href="https://lore.kernel.org/linux-input/bad@example.com/"/>
      </entry>
      <entry>
        <updated>2026-07-16T15:00:00Z</updated>
        <link href="https://lore.kernel.org/linux-input/good@example.com/"/>
      </entry>
    </feed>"""

    with caplog.at_level(logging.WARNING):
        entries = parse_feed_page(data)

    assert [e.entry_id for e in entries] == ["good@example.com"]
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
