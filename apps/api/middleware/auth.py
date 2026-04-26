"""Tenant authentication + rate-limit + usage-log middleware.

Contract
--------
- No ``Authorization`` header → falls back to the ``self`` tenant (backwards
  compatible with the single-user dev mode).
- ``Authorization: Bearer <api_key>`` → SHA-256 hashed and matched against
  ``tenants.api_key_hash``. On match the tenant_id is injected onto
  ``request.state.tenant_id`` for downstream handlers to consume via
  ``Depends(get_tenant_id)``.
- Rate limit: in-memory sliding window of the last 60 s; returns 429 when the
  request count exceeds the tenant's ``rate_limit_per_min``.
- Every request (success or failure) is logged to ``api_usage_log`` — the write
  happens on a background thread so the hot path stays unblocked.

The ``self`` tenant is exempt from bearer-token matching: it simply represents
every unauthenticated caller. This keeps the existing local/dev workflow
working without any header churn.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Make packages/core importable when the API boots from apps/api.
_PKG_CORE = Path(__file__).resolve().parents[3] / "packages" / "core"
if str(_PKG_CORE) not in sys.path:
    sys.path.insert(0, str(_PKG_CORE))

from ky_core.storage.repository import Repository  # noqa: E402

logger = logging.getLogger(__name__)

# In-process request timestamps per tenant (oldest-first deque of epoch floats)
_RATE_WINDOWS: dict[str, Deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
_RATE_WINDOW_SECONDS = 60.0

# Paths that should bypass tenancy entirely (docs, health, openapi).
_PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}


def hash_api_key(api_key: str) -> str:
    """SHA-256 hex digest — the canonical form stored in the ``tenants`` table."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _allow_request(tenant_id: str, limit_per_min: int) -> tuple[bool, int]:
    """Sliding-window check. Returns ``(allowed, current_count)``."""
    now = time.time()
    cutoff = now - _RATE_WINDOW_SECONDS
    with _RATE_LOCK:
        window = _RATE_WINDOWS[tenant_id]
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= limit_per_min:
            return False, len(window)
        window.append(now)
        return True, len(window)


def _log_usage_async(
    tenant_id: str, endpoint: str, latency_ms: int, status_code: int
) -> None:
    def _run() -> None:
        try:
            Repository().log_api_usage(
                tenant_id=tenant_id,
                endpoint=endpoint,
                latency_ms=latency_ms,
                status_code=status_code,
            )
        except Exception:  # pragma: no cover — logging must never break requests
            logger.exception("failed to persist api_usage_log row")

    threading.Thread(target=_run, daemon=True).start()


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Resolve tenant, enforce rate limit, persist usage."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)

        tenant_id = "self"
        plan = "self"
        rate_limit = 9999

        token = _extract_bearer(request)
        if token:
            try:
                repo = Repository()
                repo.ensure_self_tenant()
                tenant = repo.get_tenant_by_key_hash(hash_api_key(token))
            except Exception:  # pragma: no cover — never block on lookup failure
                logger.exception("tenant lookup failed")
                tenant = None
            if tenant is None:
                return JSONResponse(
                    status_code=401,
                    content={
                        "ok": False,
                        "data": None,
                        "error": {"code": "INVALID_API_KEY", "message": "Unknown API key"},
                    },
                )
            if not tenant.get("enabled", False):
                return JSONResponse(
                    status_code=403,
                    content={
                        "ok": False,
                        "data": None,
                        "error": {"code": "TENANT_DISABLED", "message": "Tenant disabled"},
                    },
                )
            tenant_id = tenant["tenant_id"]
            plan = tenant.get("plan", "trial")
            rate_limit = int(tenant.get("rate_limit_per_min", 60) or 60)

        request.state.tenant_id = tenant_id
        request.state.tenant_plan = plan

        allowed, current = _allow_request(tenant_id, rate_limit)
        if not allowed:
            _log_usage_async(tenant_id, path, 0, 429)
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"{current} req/min exceeds plan limit {rate_limit}",
                        "detail": {"plan": plan, "limit": rate_limit},
                    },
                },
            )

        start = time.time()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            _log_usage_async(tenant_id, path, int((time.time() - start) * 1000), 500)
            raise

        latency_ms = int((time.time() - start) * 1000)
        _log_usage_async(tenant_id, path, latency_ms, status_code)
        response.headers["X-Tenant-Id"] = tenant_id
        response.headers["X-RateLimit-Limit"] = str(rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, rate_limit - current))
        return response


def get_tenant_id(request: Request) -> str:
    """FastAPI dependency — read the tenant resolved by the middleware.

    Falls back to ``"self"`` if the middleware hasn't run (e.g. unit tests that
    mount a router without the full app stack)."""
    return getattr(request.state, "tenant_id", "self")
