"""A polite HTTP client shared by every collector.

Every request identifies the project, waits between calls to the same host, and retries
transient failures with backoff. A source that keeps failing raises, and the runner records it
as a failed source instead of stopping the whole run.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

USER_AGENT = "skillmap/0.1 (+https://www.pierreblachere.com/skills; job-market research)"


class PoliteClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 30.0, retries: int = 3,
                 per_host_interval: dict[str, float] | None = None, transport: httpx.BaseTransport | None = None,
                 max_retry_after: float = 60.0):
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.min_interval = min_interval
        self.per_host_interval = per_host_interval or {}
        self.retries = retries
        # A server can ask for a wait of hours; past this the request fails and the caller moves on.
        self.max_retry_after = max_retry_after
        self._last_call: dict[str, float] = {}
        # Sources are collected in parallel threads, one per site; a lock per host keeps two
        # threads that reach the same host to its interval.
        self._host_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self.sleep = time.sleep  # swapped out in tests

    def _wait(self, host: str) -> None:
        with self._locks_lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            interval = self.per_host_interval.get(host, self.min_interval)
            elapsed = time.monotonic() - self._last_call.get(host, 0.0)
            if elapsed < interval:
                self.sleep(interval - elapsed)
            self._last_call[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlparse(url).netloc
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                self.sleep(2**attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                retry_after = response.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
                self.sleep(min(wait, self.max_retry_after))
                continue
            return response
        raise RuntimeError("unreachable")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("GET", url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        response = self.request("GET", url, **kwargs)
        response.raise_for_status()
        return response.text

    def post_json(self, url: str, payload: Any, **kwargs: Any) -> Any:
        response = self.request("POST", url, json=payload, **kwargs)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
