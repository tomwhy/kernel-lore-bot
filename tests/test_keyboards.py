from kernel_lore_bot.delivery.keyboards import (
    follow_keyboard,
    parse_callback,
    unfollow_keyboard,
)


def test_follow_keyboard_has_one_follow_button():
    markup = follow_keyboard("t1@x.com")
    button = markup.inline_keyboard[0][0]
    assert button.text == "🔔 Follow"
    assert button.callback_data == "follow:t1@x.com"


def test_unfollow_keyboard_has_one_unfollow_button():
    button = unfollow_keyboard("t1@x.com").inline_keyboard[0][0]
    assert button.text == "🔕 Unfollow"
    assert button.callback_data == "unfollow:t1@x.com"


def test_parse_callback_reads_a_follow():
    assert parse_callback("follow:t1@x.com") == ("follow", "t1@x.com")


def test_parse_callback_reads_an_unfollow():
    assert parse_callback("unfollow:t1@x.com") == ("unfollow", "t1@x.com")


def test_parse_callback_round_trips_the_keyboards():
    markup = follow_keyboard("weird:id:with:colons@x.com")
    assert parse_callback(markup.inline_keyboard[0][0].callback_data) == (
        "follow",
        "weird:id:with:colons@x.com",
    )


def test_parse_callback_rejects_unknown_data():
    assert parse_callback("nonsense") is None
    assert parse_callback("") is None


def test_parse_callback_rejects_non_string_data():
    # After a restart PTB hands back an InvalidCallbackData object, not a str.
    assert parse_callback(None) is None
    assert parse_callback(object()) is None
