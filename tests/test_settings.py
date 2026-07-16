from pathlib import Path

from kernel_lore_bot.settings import (
    DEFAULT_MAILING_LISTS,
    PLACEHOLDER_TOKEN,
    Settings,
    load_settings,
)


def test_defaults_do_not_require_env():
    s = load_settings(env={})
    assert s.telegram_bot_token == PLACEHOLDER_TOKEN
    assert s.admin_chat_id == 0
    assert s.mailing_lists == DEFAULT_MAILING_LISTS
    assert s.loopback_hours == 4.0


def test_env_overrides_defaults():
    s = load_settings(env={
        "TELEGRAM_BOT_TOKEN": "tok",
        "ADMIN_CHAT_ID": "42",
        "SCHEDULE_INTERVAL_HOURS": "0.5",
        "KERNEL_BOT_STATE_DIR": "/tmp/state",
    })
    assert s.telegram_bot_token == "tok"
    assert s.admin_chat_id == 42
    assert s.schedule_interval_hours == 0.5
    assert s.state_dir == Path("/tmp/state")


def test_docker_secret_beats_env(tmp_path):
    (tmp_path / "telegram_bot_token").write_text("  from-secret\n")
    s = load_settings(env={"TELEGRAM_BOT_TOKEN": "from-env"}, secrets_dir=tmp_path)
    assert s.telegram_bot_token == "from-secret"


def test_schedule_interval_defaults_to_loopback_hours():
    s = load_settings(env={"LOOPBACK_HOURS": "6"})
    assert s.loopback_hours == 6.0
    assert s.schedule_interval_hours == 6.0


def test_settings_is_frozen():
    import dataclasses
    import pytest

    s = Settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.admin_chat_id = 1


def test_state_file_lives_under_state_dir():
    s = Settings(state_dir=Path("data"))
    assert s.state_file == Path("data") / "state.json"
