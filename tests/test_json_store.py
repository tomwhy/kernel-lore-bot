import json
import logging

from kernel_lore_bot.storage import STATE_VERSION, JsonStore


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
    """A subscriber who removed every list must stay empty across a restart."""
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

    store = JsonStore(path, default_lists=("netdev",))

    assert store.mailing_lists(7) == set()
