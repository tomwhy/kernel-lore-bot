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
        "subscribers": {"42": {"follows": ["t1"]}},
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


def test_legacy_migration_still_works_after_the_corruption_fix(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "follows.json").write_text(json.dumps({"t1": [1, 2]}), encoding="utf-8")

    store = JsonStore(tmp_path / "state.json")

    assert store.subscribers() == {1, 2}
    assert sorted(store.followers("t1")) == [1, 2]
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_write_leaves_no_temp_file_behind(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.add_subscriber(1)
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_migrates_both_legacy_files(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "follows.json").write_text(
        json.dumps({"t1": [1, 2], "t2": [2]}), encoding="utf-8"
    )

    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {1, 2}
    assert sorted(store.followers("t1")) == [1, 2]
    assert store.following_count(2) == 2


def test_migration_is_written_to_disk_immediately(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    JsonStore(tmp_path / "state.json")
    assert _state(tmp_path)["subscribers"] == {"1": {"follows": []}}


def test_migration_leaves_legacy_files_in_place(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    JsonStore(tmp_path / "state.json")
    assert (tmp_path / "subscribers.json").exists()


def test_migrates_subscribers_only(tmp_path):
    (tmp_path / "subscribers.json").write_text("[5]", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {5}
    assert store.following_count(5) == 0


def test_migrates_follows_only_and_implies_subscription(tmp_path):
    (tmp_path / "follows.json").write_text(json.dumps({"t1": [9]}), encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {9}
    assert store.followers("t1") == [9]


def test_no_legacy_files_means_empty_state(tmp_path):
    assert JsonStore(tmp_path / "state.json").subscribers() == set()


def test_corrupt_legacy_follows_does_not_lose_subscribers(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "follows.json").write_text("{broken", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {1}


def test_existing_state_file_wins_over_legacy_files(tmp_path):
    (tmp_path / "subscribers.json").write_text("[111]", encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "subscribers": {"222": {"follows": []}}}),
        encoding="utf-8",
    )
    assert JsonStore(tmp_path / "state.json").subscribers() == {222}
