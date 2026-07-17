import pytest
import requests

from kernel_lore_bot.http import USER_AGENT, FetchError, RequestsClient


class _FakeResponse:
    def __init__(self, content=b"body", status=200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error")


class _RecordingSession:
    def __init__(self, response=None, exc=None):
        self.response = response or _FakeResponse()
        self.exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        if self.exc:
            raise self.exc
        return self.response


def test_get_returns_body_bytes():
    session = _RecordingSession(_FakeResponse(b"<feed/>"))
    client = RequestsClient(session=session)
    assert client.get("https://lore.kernel.org/x") == b"<feed/>"


def test_get_always_sends_user_agent():
    # lore.kernel.org returns 403 without a User-Agent. This is verified behavior.
    session = _RecordingSession()
    RequestsClient(session=session).get("https://lore.kernel.org/x")
    assert session.calls[0]["headers"]["User-Agent"] == USER_AGENT


def test_get_passes_params_and_timeout():
    session = _RecordingSession()
    RequestsClient(timeout=7.5, session=session).get("u", params={"t": "123"})
    assert session.calls[0]["params"] == {"t": "123"}
    assert session.calls[0]["timeout"] == 7.5


def test_transport_error_becomes_fetch_error():
    session = _RecordingSession(exc=requests.ConnectionError("boom"))
    with pytest.raises(FetchError) as excinfo:
        RequestsClient(session=session).get("https://lore.kernel.org/x")
    assert "https://lore.kernel.org/x" in str(excinfo.value)


def test_http_status_error_becomes_fetch_error():
    session = _RecordingSession(_FakeResponse(status=403))
    with pytest.raises(FetchError):
        RequestsClient(session=session).get("https://lore.kernel.org/x")
