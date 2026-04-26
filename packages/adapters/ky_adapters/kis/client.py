"""KIS adapter — OAuth + shared token plumbing.

As of 2026-04-22 (platform branch) KIS credentials are provisioned in
``~/.gazua/shared.env``. This module now issues real OAuth2 tokens
against ``/oauth2/tokenP`` and caches them on disk so every adapter in the
process shares the same access token (KIS caps concurrent token issuance
tightly — duplicate requests within 1 minute return the same token, but
repeated re-issue still burns quota).

Per policy KIS is the sole backbone for quotes / universe / orders.
Kiwoom / Yahoo / Naver adapters are not being created for this tenant.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from ky_adapters.base import (
    AdapterError,
    AuthError,
    BaseAdapter,
    HealthStatus,
    http_request,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Token cache                                                                 #
# --------------------------------------------------------------------------- #

_TOKEN_CACHE_DIR = Path.home() / ".gazua" / "data"
_TOKEN_CACHE_PATH = _TOKEN_CACHE_DIR / "kis_token_cache.json"
_TOKEN_TTL_SECONDS = 23 * 3600  # KIS nominally issues 24h; refresh at 23h
_TOKEN_LOCK = Lock()


def _load_cached_token(app_key: str) -> Optional[dict[str, Any]]:
    """Return cached token dict if still fresh, else None.

    Cache is scoped per-app_key so switching accounts doesn't leak tokens.
    """
    if not _TOKEN_CACHE_PATH.is_file():
        return None
    try:
        data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("kis token cache unreadable: %s", exc)
        return None

    entry = data.get(app_key)
    if not entry:
        return None
    issued_at = entry.get("issued_at", 0)
    if time.time() - issued_at > _TOKEN_TTL_SECONDS:
        return None
    return entry


def _save_token(app_key: str, token: str, expires_at: str | None) -> None:
    _TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if _TOKEN_CACHE_PATH.is_file():
        try:
            data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data[app_key] = {
        "access_token": token,
        "issued_at": time.time(),
        "expires_at": expires_at,
    }
    _TOKEN_CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Restrict perms best-effort (Windows ignores chmod but harmless)
    try:
        _TOKEN_CACHE_PATH.chmod(0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #


class KISAdapter(BaseAdapter):
    source_id = "kis"
    priority = 1  # P0 — single backbone when configured

    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
        env: str | None = None,
    ) -> None:
        super().__init__()
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = (base_url or "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.env = env or "real"

    @classmethod
    def from_settings(cls) -> "KISAdapter":
        return cls(
            app_key=cls._env("KIS_APP_KEY"),
            app_secret=cls._env("KIS_APP_SECRET"),
            base_url=cls._env("KIS_BASE_URL"),
            env=cls._env("KIS_ENV"),
        )

    # --------- Token issuance ---------

    def issue_token(self, *, force: bool = False) -> str:
        """Obtain an OAuth access token, using disk cache if fresh.

        Raises :class:`AuthError` with a classified message on failure so
        callers can distinguish IP-whitelist / clock / key errors.
        """
        if not self.app_key or not self.app_secret:
            raise AuthError("KIS_APP_KEY/KIS_APP_SECRET not configured")

        with _TOKEN_LOCK:
            if not force:
                cached = _load_cached_token(self.app_key)
                if cached and cached.get("access_token"):
                    return cached["access_token"]

            url = f"{self.base_url}/oauth2/tokenP"
            body = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }
            headers = {"content-type": "application/json; charset=utf-8"}

            try:
                resp = http_request(
                    "POST",
                    url,
                    headers=headers,
                    json_body=body,
                    timeout=10.0,
                    retries=1,  # token endpoint — don't hammer it on failure
                )
            except AuthError as exc:
                # 401/403 from tokenP usually = bad key OR IP whitelist miss
                raise AuthError(self._classify_token_error(str(exc))) from exc
            except Exception as exc:
                raise AuthError(f"network failure issuing KIS token: {exc}") from exc

            data = resp.json()
            token = data.get("access_token")
            if not token:
                code = data.get("error_code") or data.get("msg_cd") or "?"
                msg = data.get("error_description") or data.get("msg1") or json.dumps(data)[:200]
                raise AuthError(self._classify_token_error(f"[{code}] {msg}"))

            expires_at = data.get("access_token_token_expired")
            _save_token(self.app_key, token, expires_at)
            logger.info("KIS token issued; expires=%s", expires_at)
            return token

    def _classify_token_error(self, raw: str) -> str:
        """Return a user-friendly error classification."""
        lowered = raw.lower()
        if "ip" in lowered or "whitelist" in lowered or "white list" in lowered or "not allowed" in lowered:
            return f"KIS IP whitelist rejection — register current IP in KIS developer center. raw={raw}"
        if "appkey" in lowered or "app key" in lowered or "invalid" in lowered or "unauthorized" in lowered:
            return f"KIS appkey/secret rejected — verify keys + real/demo env. raw={raw}"
        if "time" in lowered or "clock" in lowered or "expired" in lowered:
            return f"KIS clock skew or expired token — sync system clock. raw={raw}"
        return f"KIS token issuance failed: {raw}"

    def auth_headers(self, tr_id: str, *, tr_cont: str = "") -> dict[str, str]:
        token = self.issue_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key or "",
            "appsecret": self.app_secret or "",
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",
        }

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        if not self.app_key or not self.app_secret:
            return HealthStatus(
                ok=False,
                source_id=self.source_id,
                last_error="KIS_APP_KEY/KIS_APP_SECRET not configured",
                extra={"message": "provision KIS credentials in shared.env"},
            ).to_dict()

        start = time.monotonic()
        try:
            token = self.issue_token()
        except AuthError as exc:
            return HealthStatus(
                ok=False,
                source_id=self.source_id,
                last_error=str(exc),
                extra={"base_url": self.base_url, "env": self.env},
            ).to_dict()

        latency_ms = (time.monotonic() - start) * 1000
        return HealthStatus(
            ok=True,
            source_id=self.source_id,
            latency_ms=round(latency_ms, 2),
            extra={
                "base_url": self.base_url,
                "env": self.env,
                "token_len": len(token),
                "token_cached": latency_ms < 50,  # cache hit ~ sub-ms
            },
        ).to_dict()

    # --------- Generic caller ---------

    def call(
        self,
        method: str,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        tr_cont: str = "",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Issue an authenticated KIS REST call. Returns parsed JSON.

        Raises :class:`AdapterError` on rt_cd != '0'.
        """
        if not self.app_key or not self.app_secret:
            raise AuthError("KIS credentials not configured")

        url = f"{self.base_url}{path}"
        headers = self.auth_headers(tr_id, tr_cont=tr_cont)

        resp = http_request(
            method,
            url,
            headers=headers,
            params=params,
            json_body=body,
            timeout=timeout,
            retries=2,
        )

        try:
            data = resp.json()
        except ValueError as exc:
            raise AdapterError(f"KIS returned non-JSON for {path}: {resp.text[:200]}") from exc

        rt_cd = data.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            msg = data.get("msg1") or data.get("msg_cd") or "unknown error"
            raise AdapterError(f"KIS {tr_id} failed rt_cd={rt_cd} msg={msg}")

        return data


__all__ = ["KISAdapter"]
