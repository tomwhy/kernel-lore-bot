import gzip
from datetime import datetime, timedelta, timezone

from kernel_lore_bot.http import FetchError
from kernel_lore_bot.sources.lore.source import LoreSource

BASE = "https://lore.kernel.org"
FEED = f"{BASE}/linux-input/new.atom"

SINCE = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _mbox_gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def _thread_mbox(msg_id: str, date: str = "Thu, 16 Jul 2026 15:00:00 +0000") -> str:
    return (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: Alice Adams <alice@example.com>\n"
        f"Subject: [PATCH] {msg_id}\n"
        f"Date: {date}\n"
        f"Message-ID: <{msg_id}>\n"
        "\n"
        "body\n"
    )


def _feed(*entries: tuple[str, str]) -> bytes:
    body = "".join(
        f'<entry><updated>{updated}</updated>'
        f'<link href="{BASE}/linux-input/{msg_id}/"/></entry>'
        for msg_id, updated in entries
    )
    return (
        '<?xml version="1.0"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'
    ).encode()


def _source(client, lists=("linux-input",)):
    return LoreSource(client=client, mailing_lists=lists, base_url=BASE)


def test_fetches_thread_for_each_feed_entry(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["a@x.com"]
    assert threads[0].mailing_list == "linux-input"


def test_stops_paginating_at_an_entry_older_than_since(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("new@x.com", "2026-07-16T15:00:00Z"),
                    ("old@x.com", "2026-07-01T09:00:00Z"),  # older than SINCE
                    ("never@x.com", "2026-07-16T14:00:00Z"),  # unreachable
                )
            ],
            f"{BASE}/all/new@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("new@x.com"))],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["new@x.com"]
    # It must not have fetched the mbox for the older entry.
    assert not any("old@x.com" in c["url"] for c in client.calls)


def test_requests_next_page_one_second_before_last_entry(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(("a@x.com", "2026-07-16T15:00:00Z")),
                _feed(("b@x.com", "2026-07-16T14:00:00Z")),
                _feed(),
            ],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
            f"{BASE}/all/b@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("b@x.com"))],
        }
    )
    list(_source(client).fetch_threads(SINCE))
    feed_calls = [c for c in client.calls if c["url"] == FEED]
    # Page 2 asks for one second before page 1's last entry (15:00:00 -> 14:59:59).
    assert feed_calls[1]["params"]["t"] == "20260716145959"
    assert feed_calls[2]["params"]["t"] == "20260716135959"


def test_empty_page_ends_the_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [_feed()]})
    assert list(_source(client).fetch_threads(SINCE)) == []
    assert len([c for c in client.calls if c["url"] == FEED]) == 1


def test_same_thread_in_two_lists_is_fetched_once(conftest_fake_client):
    other_feed = f"{BASE}/netdev/new.atom"
    client = conftest_fake_client(
        {
            FEED: [_feed(("dup@x.com", "2026-07-16T15:00:00Z")), _feed()],
            other_feed: [_feed(("dup@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/dup@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("dup@x.com"))],
        }
    )
    threads = list(_source(client, lists=("linux-input", "netdev")).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["dup@x.com"]
    mbox_calls = [c for c in client.calls if "t.mbox.gz" in c["url"]]
    assert len(mbox_calls) == 1


def test_reply_message_id_seen_via_thread_is_not_refetched(conftest_fake_client):
    # The feed lists a reply; its thread mbox contains both root and reply, so
    # the reply's own feed entry must not trigger a second fetch.
    thread_text = _thread_mbox("root@x.com") + (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: Bob Brown <bob@example.com>\n"
        "Subject: Re: [PATCH] root\n"
        "Date: Thu, 16 Jul 2026 15:30:00 +0000\n"
        "Message-ID: <reply@x.com>\n"
        "In-Reply-To: <root@x.com>\n"
        "\n"
        "reply body\n"
    )
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("root@x.com", "2026-07-16T15:00:00Z"),
                    ("reply@x.com", "2026-07-16T14:30:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/root@x.com/t.mbox.gz": [_mbox_gz(thread_text)],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["root@x.com"]
    assert len([c for c in client.calls if "t.mbox.gz" in c["url"]]) == 1


def test_uncompressed_mbox_is_accepted(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_thread_mbox("a@x.com").encode()],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["a@x.com"]


def test_feed_failure_skips_that_list_but_not_the_others(conftest_fake_client):
    other_feed = f"{BASE}/netdev/new.atom"
    client = conftest_fake_client(
        {
            FEED: FetchError("boom"),
            other_feed: [_feed(("ok@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/ok@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("ok@x.com"))],
        }
    )
    threads = list(_source(client, lists=("linux-input", "netdev")).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["ok@x.com"]


def test_malformed_feed_xml_skips_that_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [b"<html>503</html>"]})
    assert list(_source(client).fetch_threads(SINCE)) == []


def test_mbox_fetch_failure_skips_only_that_thread(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("bad@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/bad@x.com/t.mbox.gz": FetchError("gone"),
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]


def test_empty_mbox_body_skips_only_that_thread(conftest_fake_client):
    # Regression for the defect-8 crash path, now exercised end to end.
    # gzip.decompress(b"") returns b"" rather than raising, so this reaches
    # parse_thread("") -> None.
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("empty@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/empty@x.com/t.mbox.gz": [b""],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]


def test_truncated_gzip_skips_only_that_thread(conftest_fake_client):
    # DEFECT 11: a cut connection yields a truncated gzip. gzip.decompress raises
    # EOFError, which is neither BadGzipFile nor OSError, so the old
    # `except gzip.BadGzipFile` missed it and the whole scrape died.
    truncated = _mbox_gz(_thread_mbox("trunc@x.com"))[:8]
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("trunc@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/trunc@x.com/t.mbox.gz": [truncated],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]
