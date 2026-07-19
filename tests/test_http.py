import pytest
import requests

from kernel_lore_bot.http import USER_AGENT, FetchError, RequestsClient


class _FakeResponse:
    def __init__(self, content=b"body", status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _RecordingSession:
    """
    Serves a scripted sequence of outcomes, one per call.

    Each item is either a _FakeResponse to return or an exception to raise.
    The last item repeats once the script runs out, so a session scripted with
    a single failure fails every time.
    """

    def __init__(self, response=None, exc=None, script=None):
        if script is None:
            script = [exc if exc is not None else (response or _FakeResponse())]
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        outcome = self.script[0] if len(self.script) == 1 else self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClock:
    """
    A monotonic clock that only moves when slept on.

    Requests themselves take zero time, which makes throttle assertions exact:
    any spacing between calls is spacing the client chose to add.
    """

    def __init__(self):
        self.now = 0.0
        self.delays = []

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


class _RecordingSleep:
    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def _client(session, **kwargs):
    """A client that neither sleeps nor throttles, unless asked to."""
    kwargs.setdefault("sleep", _RecordingSleep())
    kwargs.setdefault("min_interval", 0.0)
    return RequestsClient(session=session, **kwargs)


def test_get_returns_body_bytes():
    session = _RecordingSession(_FakeResponse(b"<feed/>"))
    assert _client(session).get("https://lore.kernel.org/x") == b"<feed/>"


def test_get_always_sends_user_agent():
    # lore.kernel.org returns 403 without a User-Agent. This is verified behavior.
    session = _RecordingSession()
    _client(session).get("https://lore.kernel.org/x")
    assert session.calls[0]["headers"]["User-Agent"] == USER_AGENT


def test_get_passes_params_and_timeout():
    session = _RecordingSession()
    _client(session, timeout=7.5).get("u", params={"t": "123"})
    assert session.calls[0]["params"] == {"t": "123"}
    assert session.calls[0]["timeout"] == 7.5


def test_transport_error_becomes_fetch_error():
    session = _RecordingSession(exc=requests.ConnectionError("boom"))
    with pytest.raises(FetchError) as excinfo:
        _client(session).get("https://lore.kernel.org/x")
    assert "https://lore.kernel.org/x" in str(excinfo.value)


def test_http_status_error_becomes_fetch_error():
    session = _RecordingSession(_FakeResponse(status=403))
    with pytest.raises(FetchError):
        _client(session).get("https://lore.kernel.org/x")


# --- retry -----------------------------------------------------------------


def test_retries_503_then_succeeds():
    # lore.kernel.org sheds load with 503 Service Unavailable under scrape
    # traffic. The next attempt usually succeeds.
    session = _RecordingSession(
        script=[_FakeResponse(status=503), _FakeResponse(b"<feed/>")]
    )
    assert _client(session).get("https://lore.kernel.org/x") == b"<feed/>"
    assert len(session.calls) == 2


def test_retries_transport_errors():
    session = _RecordingSession(
        script=[requests.ConnectionError("reset"), _FakeResponse(b"ok")]
    )
    assert _client(session).get("u") == b"ok"
    assert len(session.calls) == 2


def test_gives_up_after_max_attempts():
    session = _RecordingSession(_FakeResponse(status=503))
    with pytest.raises(FetchError) as excinfo:
        _client(session, max_attempts=3).get("https://lore.kernel.org/x")
    assert len(session.calls) == 3
    assert "3 attempts" in str(excinfo.value)


def test_does_not_retry_client_errors():
    # A 404 is not going to become a 200. Retrying only wastes the caller's time.
    session = _RecordingSession(_FakeResponse(status=404))
    with pytest.raises(FetchError):
        _client(session).get("u")
    assert len(session.calls) == 1


def test_backoff_grows_exponentially():
    sleep = _RecordingSleep()
    session = _RecordingSession(_FakeResponse(status=503))
    with pytest.raises(FetchError):
        RequestsClient(
            session=session, max_attempts=4, backoff=0.5, sleep=sleep, min_interval=0.0
        ).get("u")
    # One sleep between each pair of attempts, and none after the last.
    assert sleep.delays == [0.5, 1.0, 2.0]


def test_honors_retry_after_header():
    sleep = _RecordingSleep()
    session = _RecordingSession(
        script=[
            _FakeResponse(status=429, headers={"Retry-After": "7"}),
            _FakeResponse(b"ok"),
        ]
    )
    RequestsClient(session=session, backoff=0.5, sleep=sleep, min_interval=0.0).get("u")
    assert sleep.delays == [7.0]


def test_ignores_unparseable_retry_after():
    # Retry-After may be an HTTP date. Fall back to normal backoff rather than
    # bringing in a date parser for a hint we only need approximately.
    sleep = _RecordingSleep()
    session = _RecordingSession(
        script=[
            _FakeResponse(status=503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            _FakeResponse(b"ok"),
        ]
    )
    RequestsClient(session=session, backoff=0.5, sleep=sleep, min_interval=0.0).get("u")
    assert sleep.delays == [0.5]


def test_retry_after_is_capped():
    # A server asking us to wait an hour would stall the whole scrape.
    sleep = _RecordingSleep()
    session = _RecordingSession(
        script=[
            _FakeResponse(status=503, headers={"Retry-After": "3600"}),
            _FakeResponse(b"ok"),
        ]
    )
    RequestsClient(session=session, max_backoff=30.0, sleep=sleep, min_interval=0.0).get("u")
    assert sleep.delays == [30.0]


def test_no_retry_when_disabled():
    session = _RecordingSession(_FakeResponse(status=503))
    with pytest.raises(FetchError):
        _client(session, max_attempts=1).get("u")
    assert len(session.calls) == 1


# --- throttle --------------------------------------------------------------


def _throttled(session, clock, **kwargs):
    kwargs.setdefault("min_interval", 0.5)
    return RequestsClient(
        session=session, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs
    )


def test_first_request_is_not_delayed():
    clock = _FakeClock()
    _throttled(_RecordingSession(), clock).get("u")
    assert clock.delays == []


def test_spaces_consecutive_requests():
    # lore rate-limits a fast scrape. Requests are sequential, so holding each
    # one back by a fixed interval is enough to stay under the limit.
    clock = _FakeClock()
    client = _throttled(_RecordingSession(), clock)
    for _ in range(3):
        client.get("u")
    assert clock.delays == [0.5, 0.5]


def test_throttle_counts_time_already_spent():
    # A slow request has already provided the spacing; don't sleep the full
    # interval on top of it.
    clock = _FakeClock()
    client = _throttled(_RecordingSession(), clock, min_interval=1.0)
    client.get("u")
    clock.now += 0.75  # the network took 0.75s
    client.get("u")
    assert clock.delays == [pytest.approx(0.25)]


def test_slow_request_needs_no_throttle():
    clock = _FakeClock()
    client = _throttled(_RecordingSession(), clock, min_interval=1.0)
    client.get("u")
    clock.now += 5.0
    client.get("u")
    assert clock.delays == []


def test_retry_backoff_counts_toward_the_interval():
    # A backoff sleep already spaced the requests out; throttling on top of it
    # would double-charge the same wait.
    clock = _FakeClock()
    session = _RecordingSession(
        script=[_FakeResponse(status=503), _FakeResponse(b"ok")]
    )
    assert _throttled(session, clock, backoff=2.0, min_interval=0.5).get("u") == b"ok"
    assert clock.delays == [2.0]


def test_throttle_disabled_by_default_interval_zero():
    clock = _FakeClock()
    client = _throttled(_RecordingSession(), clock, min_interval=0.0)
    client.get("u")
    client.get("u")
    assert clock.delays == []
