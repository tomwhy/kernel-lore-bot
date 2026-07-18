"""The mailing-list index used to validate user-supplied list names."""

from __future__ import annotations

import gzip
import json

import pytest

from kernel_lore_bot.http import FetchError
from kernel_lore_bot.sources.lore.index import (
    ListIndex,
    ListIndexError,
    ListRegistry,
    fetch_list_names,
)

BASE = "https://lore.example.org"
MANIFEST_URL = f"{BASE}/manifest.js.gz"


def _manifest(*names: str) -> bytes:
    return gzip.compress(
        json.dumps({f"/{n}/git/0.git": {"description": n} for n in names}).encode()
    )


def test_fetch_list_names_takes_the_first_path_segment(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("lkml", "netdev")]})

    assert fetch_list_names(client, BASE) == frozenset({"lkml", "netdev"})


def test_multiple_epochs_collapse_to_one_name(conftest_fake_client):
    raw = gzip.compress(
        json.dumps({"/lkml/git/0.git": {}, "/lkml/git/1.git": {}}).encode()
    )
    client = conftest_fake_client({MANIFEST_URL: [raw]})

    assert fetch_list_names(client, BASE) == frozenset({"lkml"})


def test_real_fixture_parses(conftest_fake_client, fixture_bytes):
    client = conftest_fake_client({MANIFEST_URL: [fixture_bytes("manifest.js.gz")]})

    names = fetch_list_names(client, BASE)

    assert "lkml" in names
    assert "linux-media" in names


def test_whitespace_only_key_is_filtered_out(conftest_fake_client):
    """
    A key of a single space has no slashes to strip, but strip("/") alone
    leaves it as " " — dead whitespace data with no corresponding real list.
    """
    raw = gzip.compress(
        json.dumps({" ": {}, "/lkml/git/0.git": {}}).encode()
    )
    client = conftest_fake_client({MANIFEST_URL: [raw]})

    assert fetch_list_names(client, BASE) == frozenset({"lkml"})


def test_degenerate_keys_never_produce_an_empty_list_name(conftest_fake_client):
    """Keys of "/", "//", and "" must still be filtered out entirely."""
    raw = gzip.compress(
        json.dumps({"/": {}, "//": {}, "": {}, "/netdev/git/0.git": {}}).encode()
    )
    client = conftest_fake_client({MANIFEST_URL: [raw]})

    names = fetch_list_names(client, BASE)

    assert names == frozenset({"netdev"})
    assert "" not in names


def test_malformed_manifest_raises(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [gzip.compress(b"not json")]})

    with pytest.raises(ListIndexError):
        fetch_list_names(client, BASE)


def test_ungzipped_manifest_is_accepted(conftest_fake_client):
    """Some mirrors serve the manifest already decompressed."""
    client = conftest_fake_client({MANIFEST_URL: [json.dumps({"/rcu/git/0.git": {}}).encode()]})

    assert fetch_list_names(client, BASE) == frozenset({"rcu"})


def test_gzip_with_bad_crc_trailer_raises_list_index_error(conftest_fake_client):
    """
    A gzip-shaped body (valid magic + header + deflate stream) whose CRC32/
    length trailer is corrupted — e.g. one flipped bit from a flaky mirror —
    must be reported as corrupt, not silently treated as plaintext.

    gzip.decompress raises gzip.BadGzipFile for this ("Incorrect length of
    data produced" / "CRC check failed"), the SAME exception it raises for a
    body with no gzip magic at all. Distinguishing the two cases requires
    checking the magic bytes, not catching BadGzipFile.
    """
    valid = gzip.compress(json.dumps({"/rcu/git/0.git": {}}).encode())
    corrupted = bytearray(valid)
    corrupted[-1] ^= 0xFF  # damage the trailing CRC32/size, not the header
    corrupted = bytes(corrupted)
    assert corrupted.startswith(b"\x1f\x8b")  # still gzip-shaped

    client = conftest_fake_client({MANIFEST_URL: [corrupted]})

    with pytest.raises(ListIndexError, match="corrupt gzip manifest"):
        fetch_list_names(client, BASE)


def test_is_valid_is_case_insensitive():
    index = ListIndex(frozenset({"netdev"}))

    assert index.is_valid("NetDev") is True
    assert index.is_valid("nope") is False


def test_search_returns_sorted_substring_matches():
    index = ListIndex(frozenset({"linux-media", "linux-input", "netdev"}))

    assert index.search("linux") == ["linux-input", "linux-media"]
    assert index.search("LINUX") == ["linux-input", "linux-media"]
    assert index.search("nothing") == []


def test_search_respects_the_limit():
    index = ListIndex(frozenset({f"linux-{i}" for i in range(50)}))

    assert len(index.search("linux", limit=5)) == 5


def test_registry_starts_on_the_fallback(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: FetchError("lore is down")})
    registry = ListRegistry(client, BASE, fallback=("netdev",))

    assert registry.index.is_valid("netdev") is True
    assert registry.refresh() is False
    assert registry.index.is_valid("netdev") is True


def test_registry_swaps_in_a_successful_refresh(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("rcu")]})
    registry = ListRegistry(client, BASE, fallback=("netdev",))

    assert registry.refresh() is True
    assert registry.index.is_valid("rcu") is True
    assert registry.index.is_valid("netdev") is False


def test_registry_keeps_the_previous_index_when_a_refresh_fails(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("rcu")]})
    registry = ListRegistry(client, BASE, fallback=("netdev",))
    registry.refresh()

    # The route is exhausted, so the second refresh raises FetchError.
    assert registry.refresh() is False
    assert registry.index.is_valid("rcu") is True
