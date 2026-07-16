"""The network boundary. Nothing outside this module imports `requests`."""

from __future__ import annotations

from typing import Protocol

import requests

USER_AGENT = "kernel-lore-bot/1.0"


class FetchError(Exception):
    """Any transport-level failure. Hides the underlying HTTP library."""


class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None) -> bytes: ...


class RequestsClient:
    """HttpClient backed by requests. lore 403s without a User-Agent."""

    def __init__(
        self,
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = session if session is not None else requests.Session()

    def get(self, url: str, params: dict | None = None) -> bytes:
        try:
            resp = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            raise FetchError(f"GET {url} failed: {exc}") from exc
