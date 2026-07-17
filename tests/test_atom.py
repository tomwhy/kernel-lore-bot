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
