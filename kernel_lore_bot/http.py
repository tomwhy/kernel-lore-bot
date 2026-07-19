"""The network boundary. Nothing outside this module imports `requests`."""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

import requests

USER_AGENT = "kernel-lore-bot/1.0"

# Transient by nature: the server is overloaded, rate-limiting, or behind a
# proxy that lost its upstream. Everything else (403, 404, ...) is a settled
# answer and retrying it only delays the failure.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

log = logging.getLogger("kernel-bot")


class FetchError(Exception):
    """Any transport-level failure. Hides the underlying HTTP library."""


class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None) -> bytes: ...


class RequestsClient:
    """
    HttpClient backed by requests. lore 403s without a User-Agent.

    Retries transient failures with exponential backoff. lore.kernel.org sheds
    load with 503 during a multi-list scrape, and a bare `get` turns that into
    a lost digest — so retrying is the difference between a late scrape and a
    missing one.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
        max_attempts: int = 3,
        backoff: float = 1.0,
        max_backoff: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = session if session is not None else requests.Session()
        self.max_attempts = max(1, max_attempts)
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.sleep = sleep

    def get(self, url: str, params: dict | None = None) -> bytes:
        last_exc: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                resp = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                )
                if resp.status_code not in RETRYABLE_STATUSES:
                    resp.raise_for_status()
                    return resp.content
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                last_exc = requests.HTTPError(f"{resp.status_code} error")
            except requests.RequestException as exc:
                last_exc = exc
                # A settled HTTP status raised by raise_for_status above. Only
                # retryable codes reach the retry path, so this one is final.
                if isinstance(exc, requests.HTTPError):
                    raise FetchError(f"GET {url} failed: {exc}") from exc

            if attempt == self.max_attempts:
                break

            delay = min(
                retry_after
                if retry_after is not None
                else self.backoff * 2 ** (attempt - 1),
                self.max_backoff,
            )
            log.warning(
                "GET %s failed (attempt %d/%d): %s - retrying in %.1fs",
                url,
                attempt,
                self.max_attempts,
                last_exc,
                delay,
            )
            self.sleep(delay)

        raise FetchError(
            f"GET {url} failed after {self.max_attempts} attempts: {last_exc}"
        ) from last_exc


def _parse_retry_after(value: str | None) -> float | None:
    """
    Seconds from a Retry-After header, or None if it is absent or a HTTP date.

    The date form is rare here and only a hint, so falling back to our own
    backoff beats pulling in a date parser for it.
    """
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except (ValueError, AttributeError):
        return None
