import pytest

from kernel_lore_bot.storage import InMemoryStore, JsonStore


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryStore()
    return JsonStore(tmp_path / "state.json")


def test_new_store_has_no_subscribers(store):
    assert store.subscribers() == set()


def test_add_subscriber_returns_true_once(store):
    assert store.add_subscriber(1) is True
    assert store.add_subscriber(1) is False
    assert store.subscribers() == {1}


def test_remove_subscriber_returns_whether_they_were_present(store):
    store.add_subscriber(1)
    assert store.remove_subscriber(1) is True
    assert store.remove_subscriber(1) is False
    assert store.subscribers() == set()


def test_remove_subscriber_also_drops_their_follows(store):
    store.add_subscriber(1)
    store.follow("t1", 1)
    store.remove_subscriber(1)
    assert store.followers("t1") == []
    assert store.following_count(1) == 0


def test_remove_subscribers_removes_many(store):
    for chat in (1, 2, 3):
        store.add_subscriber(chat)
    store.remove_subscribers([1, 3])
    assert store.subscribers() == {2}


def test_remove_subscribers_tolerates_unknown_ids(store):
    store.add_subscriber(1)
    store.remove_subscribers([99])
    assert store.subscribers() == {1}


def test_follow_returns_true_only_the_first_time(store):
    store.add_subscriber(1)
    assert store.follow("t1", 1) is True
    assert store.follow("t1", 1) is False


def test_follow_implicitly_subscribes_an_unknown_chat(store):
    assert store.follow("t1", 7) is True
    assert 7 in store.subscribers()


def test_unfollow_returns_whether_they_were_following(store):
    store.follow("t1", 1)
    assert store.unfollow("t1", 1) is True
    assert store.unfollow("t1", 1) is False


def test_unfollow_unknown_thread_is_false(store):
    store.add_subscriber(1)
    assert store.unfollow("never", 1) is False


def test_followers_lists_every_follower_of_a_thread(store):
    store.follow("t1", 1)
    store.follow("t1", 2)
    store.follow("t2", 3)
    assert sorted(store.followers("t1")) == [1, 2]
    assert store.followers("t2") == [3]
    assert store.followers("unknown") == []


def test_following_count_counts_threads_per_chat(store):
    store.follow("t1", 1)
    store.follow("t2", 1)
    store.follow("t1", 2)
    assert store.following_count(1) == 2
    assert store.following_count(2) == 1
    assert store.following_count(999) == 0


def test_unfollow_keeps_the_chat_subscribed(store):
    store.add_subscriber(1)
    store.follow("t1", 1)
    store.unfollow("t1", 1)
    assert store.subscribers() == {1}
