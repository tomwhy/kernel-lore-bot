from pathlib import Path

import pytest

from kernel_lore_bot.http import FetchError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lore"


@pytest.fixture
def fixture_text():
    """Read a checked-in lore fixture by filename."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def fixture_bytes():
    """Read a checked-in lore fixture by filename, as raw bytes."""

    def _read(name: str) -> bytes:
        return (FIXTURE_DIR / name).read_bytes()

    return _read


class FakeHttpClient:
    """
    HttpClient backed by canned responses.

    `routes` maps a URL to a list of successive response bodies, so a paginating
    caller hitting the same URL twice gets page 1 then page 2. A route whose
    value is a FetchError instance raises instead.
    """

    def __init__(self, routes: dict[str, object]):
        self.routes = {k: list(v) if isinstance(v, list) else v for k, v in routes.items()}
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None) -> bytes:
        self.calls.append({"url": url, "params": params})
        route = self.routes.get(url)
        if route is None:
            raise FetchError(f"no route for {url}")
        if isinstance(route, FetchError):
            raise route
        if not route:
            raise FetchError(f"route exhausted for {url}")
        return route.pop(0)


@pytest.fixture
def conftest_fake_client():
    return FakeHttpClient
