import json
import logging
from datetime import datetime, timezone

from kernel_lore_bot.storage import STATE_VERSION, JsonStore
import kernel_lore_bot.storage.json_store as json_store_module


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_state_survives_a_reload(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.add_subscriber(1)
    store.follow("t1", 1)

    reloaded = JsonStore(tmp_path / "state.json")
    assert reloaded.subscribers() == {1}
    assert reloaded.followers("t1") == [1]
    assert reloaded.following_count(1) == 1


def test_written_file_has_the_documented_shape(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.follow("t1", 42)
    assert _state(tmp_path) == {
        "version": STATE_VERSION,
        "subscribers": {
            "42": {"follows": ["t1"], "mailing_lists": [], "blocked_authors": []}
        },
    }


def test_creates_missing_state_dir(tmp_path):
    store = JsonStore(tmp_path / "nested" / "deeper" / "state.json")
    store.add_subscriber(1)
    assert (tmp_path / "nested" / "deeper" / "state.json").exists()


def test_no_file_is_written_before_the_first_mutation(tmp_path):
    JsonStore(tmp_path / "state.json")
    assert not (tmp_path / "state.json").exists()


def test_corrupt_state_starts_fresh_instead_of_crashing(tmp_path):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == set()


# -- finding 2: a corrupt state.json must not silently discard subscribers --


def test_corrupt_state_is_preserved_as_a_timestamped_backup(tmp_path):
    original_bytes = b"{not json, and definitely not our schema"
    (tmp_path / "state.json").write_bytes(original_bytes)

    store = JsonStore(tmp_path / "state.json")

    assert store.subscribers() == set()
    # The corrupt file itself is gone (renamed away)...
    assert not (tmp_path / "state.json").exists()
    # ...and its exact bytes survive under a timestamped backup name.
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_bytes


def test_corrupt_state_logs_at_error_naming_the_backup(tmp_path, caplog):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        JsonStore(tmp_path / "state.json")

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "expected an ERROR-level log for a corrupt state file"
    assert any("corrupt-" in r.getMessage() for r in error_records)


def test_missing_state_file_starts_empty_with_no_backup(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == set()
    # A missing file is normal on first run — it must not be treated like
    # corruption (no backup, no error).
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_write_leaves_no_temp_file_behind(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.add_subscriber(1)
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# The legacy two-file format (subscribers.json + follows.json) is no longer
# imported; its migration tests were removed along with _migrate_legacy.


def test_legacy_files_are_ignored(tmp_path):
    (tmp_path / "subscribers.json").write_text("[111]", encoding="utf-8")
    (tmp_path / "follows.json").write_text(json.dumps({"t1": [111]}), encoding="utf-8")
    assert JsonStore(tmp_path / "state.json").subscribers() == set()


def test_state_file_round_trips_through_subscriber(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "subscribers": {"222": {"follows": ["t1", "t2"]}}}),
        encoding="utf-8",
    )
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {222}
    assert store.following_count(222) == 2

    store.add_subscriber(333)
    assert _state(tmp_path)["subscribers"] == {
        "222": {"follows": ["t1", "t2"], "mailing_lists": [], "blocked_authors": []},
        "333": {"follows": [], "mailing_lists": [], "blocked_authors": []},
    }


def test_v1_record_is_migrated_to_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "subscribers": {"7": {"follows": ["t@example.com"]}},
        }),
        encoding="utf-8",
    )

    store = JsonStore(path, default_lists=("netdev",), default_blocks=("bot",))

    assert store.mailing_lists(7) == {"netdev"}
    assert store.blocked_authors(7) == {"bot"}
    assert store.followers("t@example.com") == [7]


def test_v2_record_round_trips(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStore(path, default_lists=("netdev",))
    store.add_subscriber(7)
    store.add_lists(7, ["rcu"])
    store.block(7, "Noisy Bot")

    reloaded = JsonStore(path, default_lists=("netdev",))

    assert reloaded.mailing_lists(7) == {"netdev", "rcu"}
    assert reloaded.blocked_authors(7) == {"Noisy Bot"}


def test_written_file_is_version_2_and_sorted(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStore(path, default_lists=("netdev", "lkml"))
    store.add_subscriber(7)

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["version"] == 2
    assert raw["subscribers"]["7"]["mailing_lists"] == ["lkml", "netdev"]
    assert raw["subscribers"]["7"]["blocked_authors"] == []


def test_explicitly_empty_lists_are_not_refilled_with_defaults(tmp_path):
    """A subscriber who removed every list/block must stay empty across a
    restart, not have the configured defaults silently re-applied."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "subscribers": {
                "7": {"follows": [], "mailing_lists": [], "blocked_authors": []}
            },
        }),
        encoding="utf-8",
    )

    store = JsonStore(path, default_lists=("netdev",), default_blocks=("bot",))

    assert store.mailing_lists(7) == set()
    assert store.blocked_authors(7) == set()


# -- finding 2: one malformed record must not discard every subscriber --


def test_one_malformed_record_does_not_discard_the_others(tmp_path, caplog):
    """A single record with a non-iterable mailing_lists must not take every
    OTHER subscriber's valid follows down with it — only that one record is
    skipped, and the live file is left in place (not renamed away), backed
    up under a timestamped copy so the skipped record stays recoverable."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "subscribers": {
                "7": {
                    "follows": ["bad@example.com"],
                    "mailing_lists": 5,
                    "blocked_authors": [],
                },
                "8": {
                    "follows": ["good@example.com"],
                    "mailing_lists": ["netdev"],
                    "blocked_authors": [],
                },
            },
        }),
        encoding="utf-8",
    )

    with caplog.at_level(logging.ERROR):
        store = JsonStore(path)

    assert store.subscribers() == {8}
    assert store.followers("good@example.com") == [8]
    assert store.followers("bad@example.com") == []

    # The file itself was fine (valid JSON, our schema) — this is not the
    # file-level corruption case, so it is not renamed away; it stays live
    # and usable. But the skipped record's data must not simply evaporate
    # on the next flush, so a copy of the original bytes is preserved under
    # a backup name (see the "some malformed, some good" test below for the
    # full recoverability guarantee this provides).
    assert path.exists()
    backups = list(tmp_path.glob("*.corrupt-*"))
    assert len(backups) == 1

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("subscriber '7'" in r.getMessage() for r in error_records)


# -- fix pass 2: a skipped record must stay recoverable, not vanish forever --


def test_all_malformed_records_are_treated_as_file_corruption(tmp_path, caplog):
    """If EVERY record in an otherwise-valid-JSON file fails to parse, that
    is not "one bad row" anymore — it's a corrupt file or a systemic bug in
    Subscriber construction (see _subscriber_from_json), and must take the
    same recoverable path as genuinely unparseable JSON: the original bytes
    preserved under a timestamped backup, and the store starting empty."""
    path = tmp_path / "state.json"
    original_text = json.dumps({
        "version": 2,
        "subscribers": {
            "6": {"follows": [], "mailing_lists": 5, "blocked_authors": []},
            "7": {"follows": [], "mailing_lists": "netdev", "blocked_authors": []},
        },
    })
    path.write_text(original_text, encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        store = JsonStore(path)

    assert store.subscribers() == set()
    # The file itself is gone (renamed away), exactly like genuine
    # file-level corruption...
    assert not path.exists()
    # ...and its exact original bytes survive under a timestamped backup.
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original_text

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("corrupt-" in r.getMessage() for r in error_records)


def test_some_malformed_records_preserve_the_original_as_a_backup(tmp_path, caplog):
    """When only SOME records are malformed, the good ones load and the
    live file stays in place (not renamed) and usable — but the original
    bytes must be preserved too, because the very next mutation only
    serializes what made it into memory and would otherwise erase the
    skipped record's data for good (finding A)."""
    path = tmp_path / "state.json"
    original_text = json.dumps({
        "version": 2,
        "subscribers": {
            "7": {
                "follows": ["chat7-thread@example.com"],
                "mailing_lists": 5,
                "blocked_authors": [],
            },
            "8": {
                "follows": ["chat8-thread@example.com"],
                "mailing_lists": ["netdev"],
                "blocked_authors": [],
            },
        },
    })
    path.write_text(original_text, encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        store = JsonStore(path)

    # The good record loaded; the bad one was skipped.
    assert store.subscribers() == {8}

    # The live file is untouched (not renamed away) and still usable as-is.
    assert path.exists()
    assert (
        json.loads(path.read_text(encoding="utf-8"))["subscribers"]
        == json.loads(original_text)["subscribers"]
    )

    # A backup of the original bytes exists, holding chat 7's data.
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original_text

    # The aggregate line makes a mass-skip obvious at a glance, not just
    # discoverable by counting per-record ERRORs.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("Skipped 1 of 2" in r.getMessage() for r in error_records)

    # Trigger the next mutation — this is exactly what rewrites the live
    # file to only what made it into memory (chat 8), which is the data
    # loss finding A identified.
    store.follow("another-thread@example.com", 8)

    live_after = json.loads(path.read_text(encoding="utf-8"))
    assert set(live_after["subscribers"]) == {"8"}

    # But the backup, untouched by the flush, still holds chat 7's data —
    # the skipped record stays recoverable even after the live file moved on.
    backup_after = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup_after["subscribers"]["7"]["follows"] == ["chat7-thread@example.com"]


# -- finding 3: non-list scalars must be rejected, not silently coerced --


def test_non_list_scalars_are_rejected_not_silently_coerced(tmp_path):
    """set() accepts any iterable, so a stray string or object would
    otherwise be silently coerced into a bag of characters/keys (e.g.
    "netdev" -> {'n','e','t','d','v'}) instead of being rejected. Each
    malformed record must be skipped rather than produce bogus subscriptions.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "subscribers": {
                "1": {"follows": [], "mailing_lists": "netdev", "blocked_authors": []},
                "2": {"follows": [], "mailing_lists": {"a": 1, "b": 2}, "blocked_authors": []},
                "3": {"follows": [], "mailing_lists": [], "blocked_authors": True},
                "9": {"follows": [], "mailing_lists": ["netdev"], "blocked_authors": []},
            },
        }),
        encoding="utf-8",
    )

    store = JsonStore(path)

    # Only the well-formed record survives; none of the malformed ones
    # produced a silently-coerced bogus subscription.
    assert store.subscribers() == {9}
    assert store.mailing_lists(9) == {"netdev"}


# -- fix pass 3: collision-safe backups, no duplicate copies, copy2 failure --


def test_backup_names_do_not_collide_within_the_same_second(tmp_path, monkeypatch):
    """The file-level (os.replace) and partial-skip (shutil.copy2) backup
    paths both mint a name from the same second-granularity timestamp. If a
    partial-skip backup lands, and then — within the same second — a
    genuinely corrupt file needs a file-level backup, the second write must
    not silently overwrite the first backup's bytes."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 18, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(json_store_module, "datetime", _FrozenDatetime)

    path = tmp_path / "state.json"
    partial_text = json.dumps({
        "version": 2,
        "subscribers": {
            "7": {"follows": [], "mailing_lists": 5, "blocked_authors": []},
            "8": {"follows": [], "mailing_lists": ["netdev"], "blocked_authors": []},
        },
    })
    path.write_text(partial_text, encoding="utf-8")
    JsonStore(path)  # partial-skip backup, via shutil.copy2

    corrupt_bytes = b"{not json at all"
    path.write_bytes(corrupt_bytes)
    JsonStore(path)  # file-level backup, via os.replace, same frozen second

    backups = sorted(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 2, f"expected two distinct backups, got {backups}"
    contents = {b.read_bytes() for b in backups}
    assert partial_text.encode("utf-8") in contents
    assert corrupt_bytes in contents


def test_repeated_restarts_with_same_bad_record_do_not_duplicate_the_backup(
    tmp_path, monkeypatch
):
    """A startup crash-loop, or simply an idle bot, restarts repeatedly
    without ever reaching a mutation. Each restart must not re-copy the
    ENTIRE state file when an existing backup already holds byte-identical
    content — that's every subscriber's data duplicated on disk for no
    reason, restart after restart."""
    copy_calls = []
    real_copy2 = json_store_module.shutil.copy2

    def _tracking_copy2(src, dst):
        copy_calls.append(dst)
        return real_copy2(src, dst)

    monkeypatch.setattr(json_store_module.shutil, "copy2", _tracking_copy2)

    path = tmp_path / "state.json"
    original_text = json.dumps({
        "version": 2,
        "subscribers": {
            "7": {"follows": [], "mailing_lists": 5, "blocked_authors": []},
            "8": {"follows": [], "mailing_lists": ["netdev"], "blocked_authors": []},
        },
    })
    path.write_text(original_text, encoding="utf-8")

    JsonStore(path)  # first restart: bad record still present, must back up
    JsonStore(path)  # second restart: same bad record, must NOT back up again

    assert len(copy_calls) == 1, "shutil.copy2 must only run once across both restarts"
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original_text


def test_unreadable_existing_backup_does_not_crash_the_store(tmp_path):
    """The dedup comparison reads existing `.corrupt-*` siblings to decide
    whether to skip writing a fresh backup. If one of those siblings cannot
    be read (e.g. it is a directory rather than a file, from a stray mkdir
    or manual tampering) the read must not propagate an OSError out of the
    constructor and crash the bot at startup. An unreadable sibling must be
    treated as "not a match" so the scan continues and a fresh backup is
    still written."""
    path = tmp_path / "state.json"
    original_text = json.dumps({
        "version": 2,
        "subscribers": {
            "7": {"follows": [], "mailing_lists": 5, "blocked_authors": []},
            "8": {
                "follows": ["good@example.com"],
                "mailing_lists": ["netdev"],
                "blocked_authors": [],
            },
        },
    })
    path.write_text(original_text, encoding="utf-8")

    # Plant an unreadable ".corrupt-*" sibling that matches the dedup glob.
    (tmp_path / "state.json.corrupt-20200101000000").mkdir()

    store = JsonStore(path)

    # (a) the store still loads the good records.
    assert store.subscribers() == {8}
    assert store.followers("good@example.com") == [8]

    # (b) the live state.json is still present and usable.
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["subscribers"]["8"]["follows"] == [
        "good@example.com"
    ]

    # (c) a fresh backup was written despite the unreadable sibling.
    file_backups = [b for b in tmp_path.glob("state.json.corrupt-*") if b.is_file()]
    assert len(file_backups) == 1
    assert file_backups[0].read_text(encoding="utf-8") == original_text


def test_partial_skip_backup_failure_does_not_crash_the_store(tmp_path, monkeypatch, caplog):
    """The `except OSError` around the partial-skip copy is what keeps the
    bot STARTING when the backup cannot be written (permissions, disk full,
    a read-only directory). It must log loudly and continue — a backup
    failure must never take the bot down."""

    def _raise_oserror(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(json_store_module.shutil, "copy2", _raise_oserror)

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "subscribers": {
                "7": {"follows": [], "mailing_lists": 5, "blocked_authors": []},
                "8": {
                    "follows": ["good@example.com"],
                    "mailing_lists": ["netdev"],
                    "blocked_authors": [],
                },
            },
        }),
        encoding="utf-8",
    )

    with caplog.at_level(logging.ERROR):
        store = JsonStore(path)

    # (a) the store still loads the good records.
    assert store.subscribers() == {8}
    assert store.followers("good@example.com") == [8]

    # (b) the live state.json is still present and usable.
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["subscribers"]["8"]["follows"] == [
        "good@example.com"
    ]

    # No backup exists — the copy never succeeded.
    assert list(tmp_path.glob("state.json.corrupt-*")) == []

    # (c) an error is logged.
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("could not back it up" in r.getMessage() for r in error_records)
