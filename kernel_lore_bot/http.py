"""The network boundary. Nothing outside this module imports `requests`."""

from __future__ import annotations

import logging
import threading
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

    Two defences against lore's rate limiting, which a multi-list scrape trips
    easily:

    * `min_interval` paces outgoing requests so we stay under the limit rather
      than discovering it. A scrape issues its requests sequentially, so a
      floor on the gap between them is enough — no token bucket needed.
    * Transient failures (503 and friends) are retried with exponential
      backoff, because staying under a limit is best-effort and a bare `get`
      turns one refusal into a lost digest.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
        max_attempts: int = 3,
        backoff: float = 1.0,
        max_backoff: float = 30.0,
        min_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = session if session is not None else requests.Session()
        self.max_attempts = max(1, max_attempts)
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.min_interval = min_interval
        self.sleep = sleep
        self.monotonic = monotonic
        # The scrape runs on a worker thread while the bot's list-index refresh
        # may fire on another, and both share one client. Without the lock two
        # threads could read the same "last request" stamp and both decide they
        # owe no wait.
        self._lock = threading.Lock()
        self._last_request: float | None = None

    def _throttle(self) -> None:
        """
        Block until `min_interval` has passed since the previous request began.

        The slot is claimed under the lock but waited on outside it, so a second
        caller reserves the *next* slot instead of queueing behind this one's
        network I/O. Time already spent — a slow response, a retry backoff —
        counts toward the interval rather than being added to.
        """
        with self._lock:
            now = self.monotonic()
            if self._last_request is None:
                self._last_request = now
                return
            starts_at = max(now, self._last_request + self.min_interval)
            self._last_request = starts_at

        wait = starts_at - now
        if wait > 0:
            self.sleep(wait)

    def get(self, url: str, params: dict | None = None) -> bytes:
        last_exc: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            self._throttle()
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
