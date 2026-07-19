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
    """Return (source, lists) — lists are now a fetch_threads argument."""
    return LoreSource(client=client, base_url=BASE), tuple(lists)


def test_fetches_thread_for_each_feed_entry(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
        }
    )
    source, lists = _source(client)
    threads = list(source.fetch_threads(SINCE, lists))
    assert [t.id for t in threads] == ["a@x.com"]
    assert threads[0].mailing_lists == frozenset({"linux-input"})


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
    source, lists = _source(client)
    threads = list(source.fetch_threads(SINCE, lists))
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
    source, lists = _source(client)
    list(source.fetch_threads(SINCE, lists))
    feed_calls = [c for c in client.calls if c["url"] == FEED]
    # Page 2 asks for one second before page 1's last entry (15:00:00 -> 14:59:59).
    assert feed_calls[1]["params"]["t"] == "20260716145959"
    assert feed_calls[2]["params"]["t"] == "20260716135959"


def test_empty_page_ends_the_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [_feed()]})
    source, lists = _source(client)
    assert list(source.fetch_threads(SINCE, lists)) == []
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
    source, lists = _source(client, lists=("linux-input", "netdev"))
    threads = list(source.fetch_threads(SINCE, lists))
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
    source, lists = _source(client)
    threads = list(source.fetch_threads(SINCE, lists))
    assert [t.id for t in threads] == ["root@x.com"]
    assert len([c for c in client.calls if "t.mbox.gz" in c["url"]]) == 1


def test_uncompressed_mbox_is_accepted(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_thread_mbox("a@x.com").encode()],
        }
    )
    source, lists = _source(client)
    assert [t.id for t in source.fetch_threads(SINCE, lists)] == ["a@x.com"]


def test_feed_failure_skips_that_list_but_not_the_others(conftest_fake_client):
    other_feed = f"{BASE}/netdev/new.atom"
    client = conftest_fake_client(
        {
            FEED: FetchError("boom"),
            other_feed: [_feed(("ok@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/ok@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("ok@x.com"))],
        }
    )
    source, lists = _source(client, lists=("linux-input", "netdev"))
    threads = list(source.fetch_threads(SINCE, lists))
    assert [t.id for t in threads] == ["ok@x.com"]


def test_malformed_feed_xml_skips_that_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [b"<html>503</html>"]})
    source, lists = _source(client)
    assert list(source.fetch_threads(SINCE, lists)) == []


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
    source, lists = _source(client)
    assert [t.id for t in source.fetch_threads(SINCE, lists)] == ["good@x.com"]


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
    source, lists = _source(client)
    assert [t.id for t in source.fetch_threads(SINCE, lists)] == ["good@x.com"]


def _corrupted_gzip(text: str) -> bytes:
    # Flip a byte well inside the compressed body (past the 10-byte gzip
    # header) so gzip.decompress raises zlib.error rather than BadGzipFile.
    # Index 20 was verified empirically to reliably trigger
    # "invalid code -- missing end-of-block" for this fixture's payload shape.
    data = bytearray(_mbox_gz(text))
    data[20] ^= 0xFF
    return bytes(data)


def test_corrupted_gzip_bit_flip_skips_only_that_thread(conftest_fake_client):
    # DEFECT 16: a bit flipped mid-stream (not a truncation) makes
    # gzip.decompress raise zlib.error, which is neither gzip.BadGzipFile nor
    # EOFError. The old code only caught those two, so this exception used to
    # propagate out of fetch_threads() and kill the entire run.
    corrupted = _corrupted_gzip(_thread_mbox("corrupt@x.com"))
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("corrupt@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/corrupt@x.com/t.mbox.gz": [corrupted],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    source, lists = _source(client)
    assert [t.id for t in source.fetch_threads(SINCE, lists)] == ["good@x.com"]


def test_crc_corrupted_gzip_mbox_is_skipped_with_a_warning_not_treated_as_plaintext(
    conftest_fake_client, caplog
):
    """
    Finding 8: index.py's fetch_list_names had this identical bug and was
    fixed there -- gzip.BadGzipFile is raised not only for "no gzip magic"
    but ALSO for a CRC32/length trailer mismatch (a gzip-shaped body with a
    valid header and deflate stream, but a corrupted trailer -- e.g. one
    flipped bit from a flaky mirror). The old `except gzip.BadGzipFile: pass`
    fallback could not tell the two apart and treated CRC-corrupted bytes as
    already-decompressed plaintext, handed them to the mbox parser, found no
    "From " separator, and silently returned None at DEBUG level -- a
    corrupt download was indistinguishable from an empty thread.

    _fetch_thread must apply index.py's magic-byte check: decide "is this
    gzip at all" from the leading 0x1f 0x8b bytes, not from which exception
    gzip.decompress happens to raise, and log a WARNING (not DEBUG) when the
    magic bytes are present but decompression still fails.
    """
    import logging

    valid = _mbox_gz(_thread_mbox("crc@x.com"))
    corrupted = bytearray(valid)
    corrupted[-1] ^= 0xFF  # damage the trailing CRC32/size, not the header
    corrupted = bytes(corrupted)
    assert corrupted.startswith(b"\x1f\x8b")  # still gzip-shaped

    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("crc@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/crc@x.com/t.mbox.gz": [corrupted],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    source, lists = _source(client)

    with caplog.at_level(logging.WARNING):
        threads = list(source.fetch_threads(SINCE, lists))

    assert [t.id for t in threads] == ["good@x.com"]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("crc@x.com" in r.getMessage() or "Corrupt" in r.getMessage()
               for r in warning_records)


def test_corrupted_gzip_in_one_list_does_not_kill_a_later_list(conftest_fake_client):
    # DEFECT 16, end-to-end across lists: the corrupted gzip must not blow up
    # fetch_threads() itself, so a second, unrelated list in the same run
    # still gets fetched.
    other_feed = f"{BASE}/netdev/new.atom"
    corrupted = _corrupted_gzip(_thread_mbox("corrupt@x.com"))
    client = conftest_fake_client(
        {
            FEED: [_feed(("corrupt@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/corrupt@x.com/t.mbox.gz": [corrupted],
            other_feed: [_feed(("ok@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/ok@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("ok@x.com"))],
        }
    )
    source, lists = _source(client, lists=("linux-input", "netdev"))
    threads = list(source.fetch_threads(SINCE, lists))
    assert [t.id for t in threads] == ["ok@x.com"]


def test_pagination_terminates_when_server_repeats_the_same_page(conftest_fake_client):
    # DEFECT 17: a server that ignores the `t` param and keeps returning the
    # same non-empty page must not hang the run. Every page has the same
    # entry, so the computed next `t` never advances -- the progress guard
    # must end the list instead of looping forever.
    same_page = _feed(("stuck@x.com", "2026-07-16T15:00:00Z"))
    client = conftest_fake_client({FEED: [same_page] * 500})
    source, lists = _source(client)
    threads = list(source.fetch_threads(SINCE, lists))
    # Fetched the one entry the (broken) server ever offers, then stopped.
    feed_calls = [c for c in client.calls if c["url"] == FEED]
    assert len(feed_calls) < 500
    assert len(feed_calls) <= 3


def test_entry_url_follows_a_custom_base_url(conftest_fake_client):
    # Finding 4: pointing LoreSource at a mirror must not leave Entry.url
    # hardcoded to lore.kernel.org — every "View thread" link would point at
    # the wrong host otherwise.
    mirror = "https://mirror.example.com"
    mirror_feed = f"{mirror}/linux-input/new.atom"
    client = conftest_fake_client(
        {
            mirror_feed: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{mirror}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
        }
    )
    source = LoreSource(client=client, base_url=mirror)
    threads = list(source.fetch_threads(SINCE, ("linux-input",)))
    assert threads[0].roots[0].entry.url == f"{mirror}/all/a@x.com"


def test_cross_posted_thread_carries_every_list(conftest_fake_client):
    """One thread on two lists is fetched once and tagged with both lists."""
    other_feed = f"{BASE}/netdev/new.atom"
    atom = _feed(("shared@x.com", "2026-07-16T15:00:00Z"))
    mbox = _mbox_gz(_thread_mbox("shared@x.com"))
    client = conftest_fake_client(
        {
            FEED: [atom, _feed()],
            other_feed: [atom, _feed()],
            f"{BASE}/all/shared@x.com/t.mbox.gz": [mbox],
        }
    )
    source = LoreSource(client=client, base_url=BASE)

    threads = source.fetch_threads(SINCE, ("linux-input", "netdev"))

    assert len(threads) == 1
    assert threads[0].mailing_lists == frozenset({"linux-input", "netdev"})
    # The mbox is downloaded once, not once per list.
    mbox_calls = [c for c in client.calls if "t.mbox.gz" in c["url"]]
    assert len(mbox_calls) == 1


def test_refetch_from_second_list_unions_lists_instead_of_overwriting(conftest_fake_client):
    """
    Regression: a reply that lands between list A's fetch and list B's feed
    poll must not cause list B's re-fetch of the thread to drop list A's tag.

    List A's feed surfaces only the thread root, and the mbox downloaded at
    that point contains just the root. List B's feed surfaces a reply that
    arrived later and was not present in that first mbox, so its message-id
    is unseen and `_fetch_thread` runs again for it -- this time the mbox
    includes both root and reply. The resulting single Thread must carry
    both list names, not just the one from the second fetch.
    """
    other_feed = f"{BASE}/netdev/new.atom"
    reply_mbox_text = _thread_mbox("root@x.com") + (
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
            FEED: [_feed(("root@x.com", "2026-07-16T15:00:00Z")), _feed()],
            other_feed: [_feed(("reply@x.com", "2026-07-16T15:30:00Z")), _feed()],
            f"{BASE}/all/root@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("root@x.com"))],
            f"{BASE}/all/reply@x.com/t.mbox.gz": [_mbox_gz(reply_mbox_text)],
        }
    )
    source, lists = _source(client, lists=("linux-input", "netdev"))

    threads = list(source.fetch_threads(SINCE, lists))

    assert [t.id for t in threads] == ["root@x.com"]
    assert threads[0].mailing_lists == frozenset({"linux-input", "netdev"})


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
    source, lists = _source(client)
    assert [t.id for t in source.fetch_threads(SINCE, lists)] == ["good@x.com"]


# -- fetch_threads_by_id (task 6b) -----------------------------------


def test_fetch_threads_by_id_fetches_each_id_by_message_id(conftest_fake_client):
    client = conftest_fake_client(
        {
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
            f"{BASE}/all/b@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("b@x.com"))],
        }
    )
    source, _ = _source(client)
    threads = source.fetch_threads_by_id(["a@x.com", "b@x.com"])
    assert sorted(t.id for t in threads) == ["a@x.com", "b@x.com"]


def test_fetch_threads_by_id_has_no_mailing_list(conftest_fake_client):
    # A thread reached by id, not by feed, has no known list -- followers
    # bypass visible_for entirely, so it does not need one.
    client = conftest_fake_client(
        {f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))]}
    )
    source, _ = _source(client)
    threads = source.fetch_threads_by_id(["a@x.com"])
    assert threads[0].mailing_lists == frozenset()


def test_fetch_threads_by_id_skips_a_failed_fetch_but_not_the_rest(conftest_fake_client):
    client = conftest_fake_client(
        {
            f"{BASE}/all/dead@x.com/t.mbox.gz": FetchError("gone"),
            f"{BASE}/all/alive@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("alive@x.com"))],
        }
    )
    source, _ = _source(client)
    threads = source.fetch_threads_by_id(["dead@x.com", "alive@x.com"])
    assert [t.id for t in threads] == ["alive@x.com"]


def test_fetch_threads_by_id_of_nothing_makes_no_requests(conftest_fake_client):
    client = conftest_fake_client({})
    source, _ = _source(client)
    assert source.fetch_threads_by_id([]) == []
    assert client.calls == []
