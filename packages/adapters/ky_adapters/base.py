"""Shared base for all ky_adapters.

Every data-source adapter in this package MUST inherit from :class:`BaseAdapter`
and implement a minimal contract (``healthcheck``, ``close``). The module also
exposes a small HTTP helper built on ``httpx`` with:

- 10s default timeout (overridable per call)
- Up to 3 retries on network/5xx errors with exponential backoff
- Rich error typing (``AdapterError``, ``AuthError``, ``RateLimitError``)

Adapters MUST NOT reach into external state (e.g. os.environ) directly: use
``from_settings`` factories that take env vars once and close over them.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

import httpx

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Env loading                                                                 #
# --------------------------------------------------------------------------- #

_SHARED_ENV = Path.home() / ".gazua" / "shared.env"
try:
    from dotenv import load_dotenv

    if _SHARED_ENV.is_file():
        load_dotenv(_SHARED_ENV, override=False)
except ImportError:  # pragma: no cover — optional dep
    pass


def shared_env_loaded() -> bool:
    return _SHARED_ENV.is_file()


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class AdapterError(Exception):
    """Base class for all adapter errors."""


class AuthError(AdapterError):
    """Raised when the adapter cannot authenticate (401/403, missing key)."""


class RateLimitError(AdapterError):
    """Raised when the upstream throttles us (HTTP 429, vendor-specific codes)."""


# --------------------------------------------------------------------------- #
# HTTP helper                                                                 #
# --------------------------------------------------------------------------- #


DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 3
BACKOFF_BASE = 0.5  # seconds


def http_request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """Execute an HTTP request with retries and typed errors.

    Retries on connection errors and 5xx responses. Does not retry 4xx.
    """
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    last_exc: Exception | None = None
    try:
        for attempt in range(retries + 1):
            try:
                resp = http.request(
                    method.upper(),
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                    timeout=timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt >= retries:
                    raise AdapterError(f"network failure after {retries} retries: {exc}") from exc
                _sleep_backoff(attempt)
                continue

            status = resp.status_code
            if status in (401, 403):
                raise AuthError(f"{method} {url} → HTTP {status}")
            if status == 429:
                if attempt >= retries:
                    raise RateLimitError(f"{method} {url} → HTTP 429 after {retries} retries")
                _sleep_backoff(attempt)
                continue
            if 500 <= status < 600:
                if attempt >= retries:
                    raise AdapterError(f"{method} {url} → HTTP {status} after {retries} retries")
                _sleep_backoff(attempt)
                continue

            return resp
        # unreachable — loop always returns or raises
        raise AdapterError(f"exhausted retries: {last_exc}")
    finally:
        if owns_client:
            http.close()


def _sleep_backoff(attempt: int) -> None:
    delay = BACKOFF_BASE * (2**attempt)
    time.sleep(delay)


# --------------------------------------------------------------------------- #
# BaseAdapter                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class HealthStatus:
    ok: bool
    latency_ms: float | None = None
    last_error: str | None = None
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source_id": self.source_id,
            "latency_ms": self.latency_ms,
            "last_error": self.last_error,
            **self.extra,
        }


class BaseAdapter:
    """Contract every adapter must satisfy."""

    source_id: ClassVar[str] = "base"
    priority: ClassVar[int] = 999

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._owns_client = client is None

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        """Return a serialisable health dict. Subclasses should override."""
        return HealthStatus(ok=False, source_id=self.source_id, last_error="not implemented").to_dict()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "BaseAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------- Helpers ---------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json_body: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> httpx.Response:
        return http_request(
            method,
            url,
            params=params,
            headers=headers,
            json_body=json_body,
            timeout=timeout,
            retries=retries,
            client=self._client,
        )

    @staticmethod
    def _env(name: str) -> Optional[str]:
        val = os.environ.get(name)
        if val is None or val.strip() == "":
            return None
        return val.strip()

    @staticmethod
    def _timed_ok(latency_ms: float, source_id: str, extra: dict | None = None) -> dict[str, Any]:
        return HealthStatus(
            ok=True, latency_ms=latency_ms, source_id=source_id, extra=extra or {}
        ).to_dict()

    @staticmethod
    def _timed_fail(source_id: str, err: str) -> dict[str, Any]:
        return HealthStatus(ok=False, source_id=source_id, last_error=err).to_dict()
