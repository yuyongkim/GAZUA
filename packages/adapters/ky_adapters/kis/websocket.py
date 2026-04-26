"""KIS WebSocket adapter — real-time orderbook / tick streaming.

Activated 2026-04-22 on the ``platform`` branch. This module wires up KIS's
WebSocket spec:

  1. POST ``/oauth2/Approval`` with ``grant_type=client_credentials`` + app
     key/secret → returns ``approval_key`` (short-lived, ~24h).
  2. Open ``ws://ops.koreainvestment.com:21000`` (실전) via the ``websockets``
     library.
  3. Subscribe by sending a JSON envelope:

        {
          "header": {
            "approval_key": "...",
            "custtype": "P",
            "tr_type": "1",              # "1" = subscribe, "2" = unsubscribe
            "content-type": "utf-8"
          },
          "body": {
            "input": {
              "tr_id": "H0STASP0",       # 호가
              "tr_key": "005930"
            }
          }
        }

  4. Incoming frames are either:
     - JSON control frames (subscribe ack, PINGPONG, error) — identified by a
       leading '{' character.
     - Pipe-delimited data frames: ``<encrypt_flag>|<tr_id>|<count>|<payload>``
       where ``payload`` is ``^``-delimited fields, and ``count`` items are
       concatenated back-to-back.

  5. H0STASP0 (호가) payload has 44 fields per record, H0STCNT0 (체결) has
     46. Parsers below map the canonical fields we expose via SSE.

Notes:
- Approval key is cached on disk (``~/.gazua/data/kis_approval_cache.json``)
  with a conservative 6h TTL; force=True re-issues.
- Subscription cap: KIS limits concurrent subscriptions to 41 total (and
  operationally we clamp to 10 symbols per stream type in the router to be
  friendly).
- All public APIs are ``async``. Callers use
  :class:`KISWebSocketClient` as an async context manager.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from ky_adapters.base import AuthError, http_request

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Approval key cache                                                          #
# --------------------------------------------------------------------------- #

_APPROVAL_CACHE_DIR = Path.home() / ".gazua" / "data"
_APPROVAL_CACHE_PATH = _APPROVAL_CACHE_DIR / "kis_approval_cache.json"
_APPROVAL_TTL_SECONDS = 6 * 3600  # conservative — KIS doesn't document exact TTL
_APPROVAL_LOCK = asyncio.Lock()

# KIS WebSocket URLs
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_DEMO = "ws://ops.koreainvestment.com:31000"  # paper trading

# TR ids we expose
TR_ORDERBOOK = "H0STASP0"  # 호가 (10-level ladder, 44 fields)
TR_TICK = "H0STCNT0"       # 체결 (tick-by-tick, 46 fields)

# Field schemas (Korean-official field names kept as _raw_* for debugging;
# we project to stable snake_case keys for the SSE consumer).
# Source: KIS API docs (실시간 시세 / 실시간 체결가).
_ORDERBOOK_FIELDS = [
    "mksa_rlux_yn",      # 유가증권단축종목코드? kept for ordering
    "bsop_hour",         # 영업시간
    "hour_cls_code",     # 시간구분코드
    "askp1", "askp2", "askp3", "askp4", "askp5",
    "askp6", "askp7", "askp8", "askp9", "askp10",
    "bidp1", "bidp2", "bidp3", "bidp4", "bidp5",
    "bidp6", "bidp7", "bidp8", "bidp9", "bidp10",
    "askp_rsqn1", "askp_rsqn2", "askp_rsqn3", "askp_rsqn4", "askp_rsqn5",
    "askp_rsqn6", "askp_rsqn7", "askp_rsqn8", "askp_rsqn9", "askp_rsqn10",
    "bidp_rsqn1", "bidp_rsqn2", "bidp_rsqn3", "bidp_rsqn4", "bidp_rsqn5",
    "bidp_rsqn6", "bidp_rsqn7", "bidp_rsqn8", "bidp_rsqn9", "bidp_rsqn10",
    "total_askp_rsqn", "total_bidp_rsqn",
]

_TICK_FIELDS = [
    "mksa_cls_code",     # 유가증권구분코드
    "stck_cntg_hour",    # 체결시각 HHMMSS
    "stck_prpr",         # 현재가
    "prdy_vrss_sign",    # 전일대비부호
    "prdy_vrss",         # 전일대비
    "prdy_ctrt",         # 전일대비율
    "wghn_avrg_stck_prc",  # 가중평균주식가격
    "stck_oprc",         # 시가
    "stck_hgpr",         # 고가
    "stck_lwpr",         # 저가
    "askp1",             # 매도호가1
    "bidp1",             # 매수호가1
    "cntg_vol",          # 체결거래량
    "acml_vol",          # 누적거래량
    "acml_tr_pbmn",      # 누적거래대금
    "seln_cntg_csnu",    # 매도체결건수
    "shnu_cntg_csnu",    # 매수체결건수
    "ntby_cntg_csnu",    # 순매수체결건수
    "cttr",              # 체결강도
    "seln_cntg_smtn",    # 총매도수량
    "shnu_cntg_smtn",    # 총매수수량
    "ccld_dvsn",         # 체결구분 (1매수/3매도/5장전)
    "shnu_rate",         # 매수비율
    "prdy_vol_vrss_acml_vol_rate",  # 전일거래량대비등락율
    "oprc_hour",         # 시가시간
    "oprc_vrss_prpr_sign",  # 시가대비부호
    "oprc_vrss_prpr",    # 시가대비
    "hgpr_hour",         # 고가시간
    "hgpr_vrss_prpr_sign",
    "hgpr_vrss_prpr",
    "lwpr_hour",
    "lwpr_vrss_prpr_sign",
    "lwpr_vrss_prpr",
    "bsop_date",         # 영업일자
    "new_mkop_cls_code", # 신 장운영구분코드
    "trht_yn",           # 거래정지여부
    "askp_rsqn1",
    "bidp_rsqn1",
    "total_askp_rsqn",
    "total_bidp_rsqn",
    "vol_tnrt",          # 거래량회전율
    "prdy_smns_hour_acml_vol",
    "prdy_smns_hour_acml_vol_rate",
    "hour_cls_code",
    "mkop_cls_code",
    "trht_yn_raw",       # spare
]


# --------------------------------------------------------------------------- #
# Approval key                                                                #
# --------------------------------------------------------------------------- #


def _load_cached_approval(app_key: str) -> Optional[str]:
    if not _APPROVAL_CACHE_PATH.is_file():
        return None
    try:
        data = json.loads(_APPROVAL_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    entry = data.get(app_key)
    if not entry:
        return None
    if time.time() - entry.get("issued_at", 0) > _APPROVAL_TTL_SECONDS:
        return None
    return entry.get("approval_key")


def _save_approval(app_key: str, approval_key: str) -> None:
    _APPROVAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if _APPROVAL_CACHE_PATH.is_file():
        try:
            data = json.loads(_APPROVAL_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data[app_key] = {"approval_key": approval_key, "issued_at": time.time()}
    _APPROVAL_CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        _APPROVAL_CACHE_PATH.chmod(0o600)
    except OSError:
        pass


def issue_approval_key(
    *,
    app_key: str,
    app_secret: str,
    base_url: str = "https://openapi.koreainvestment.com:9443",
    force: bool = False,
) -> str:
    """Synchronously fetch (or reuse cached) ``approval_key`` from KIS.

    KIS's Approval endpoint is a normal HTTPS POST, so we use the shared
    ``http_request`` helper rather than an async client — this keeps it
    compatible with the existing REST OAuth flow.
    """
    if not force:
        cached = _load_cached_approval(app_key)
        if cached:
            return cached

    url = f"{base_url.rstrip('/')}/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "secretkey": app_secret,  # NOTE: Approval uses `secretkey`, not `appsecret`
    }
    headers = {"content-type": "application/json; charset=utf-8"}
    try:
        resp = http_request(
            "POST", url, headers=headers, json_body=body, timeout=10.0, retries=1
        )
    except AuthError as exc:
        raise AuthError(f"KIS Approval rejected: {exc}") from exc
    except Exception as exc:
        raise AuthError(f"network failure issuing KIS approval_key: {exc}") from exc

    data = resp.json()
    approval = data.get("approval_key")
    if not approval:
        raise AuthError(f"KIS Approval returned no key: {json.dumps(data)[:200]}")

    _save_approval(app_key, approval)
    logger.info("KIS approval_key issued; len=%d", len(approval))
    return approval


# --------------------------------------------------------------------------- #
# Frame parsing                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class KISFrame:
    """A parsed inbound frame from the KIS WebSocket."""

    kind: str              # "data" | "control" | "pingpong" | "error"
    tr_id: str | None = None
    tr_key: str | None = None
    records: list[dict[str, Any]] | None = None
    raw: str = ""
    control: dict[str, Any] | None = None


def _parse_pipe_frame(raw: str, field_schema: list[str]) -> list[dict[str, Any]]:
    """Split ``payload`` fields into ``count`` records of ``len(schema)`` each.

    KIS frame format: ``encrypt|tr_id|count|v1^v2^...``
    """
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return []
    try:
        count = int(parts[2])
    except ValueError:
        count = 1
    tokens = parts[3].split("^")
    width = len(field_schema)
    out: list[dict[str, Any]] = []
    for i in range(count):
        chunk = tokens[i * width : (i + 1) * width]
        if not chunk:
            continue
        rec = {field_schema[j] if j < width else f"f{j}": chunk[j] for j in range(len(chunk))}
        out.append(rec)
    return out


def parse_frame(raw: str) -> KISFrame:
    """Classify + decode a KIS WS frame."""
    if not raw:
        return KISFrame(kind="error", raw=raw)
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return KISFrame(kind="error", raw=raw)
        header = obj.get("header") or {}
        body = obj.get("body") or {}
        tr_id = header.get("tr_id")
        # PINGPONG arrives as a control message with tr_id PINGPONG
        if tr_id == "PINGPONG":
            return KISFrame(kind="pingpong", tr_id=tr_id, raw=raw, control=obj)
        rt_cd = body.get("rt_cd")
        if rt_cd and rt_cd != "0":
            return KISFrame(kind="error", tr_id=tr_id, raw=raw, control=obj)
        return KISFrame(kind="control", tr_id=tr_id, raw=raw, control=obj)

    # Pipe-delimited data frame
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return KISFrame(kind="error", raw=raw)
    tr_id = parts[1]
    if tr_id == TR_ORDERBOOK:
        records = _parse_pipe_frame(raw, _ORDERBOOK_FIELDS)
        tr_key = records[0].get("mksa_rlux_yn") if records else None
        return KISFrame(kind="data", tr_id=tr_id, tr_key=tr_key, records=records, raw=raw)
    if tr_id == TR_TICK:
        records = _parse_pipe_frame(raw, _TICK_FIELDS)
        tr_key = records[0].get("mksa_cls_code") if records else None
        return KISFrame(kind="data", tr_id=tr_id, tr_key=tr_key, records=records, raw=raw)
    return KISFrame(kind="data", tr_id=tr_id, records=[], raw=raw)


def normalise_orderbook_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a H0STASP0 record to a stable 10-level ladder."""
    levels: list[dict[str, Any]] = []
    for i in range(1, 11):
        levels.append({
            "level": i,
            "ask_price": rec.get(f"askp{i}"),
            "ask_qty": rec.get(f"askp_rsqn{i}"),
            "bid_price": rec.get(f"bidp{i}"),
            "bid_qty": rec.get(f"bidp_rsqn{i}"),
        })
    return {
        "ts": rec.get("bsop_hour"),
        "total_ask_qty": rec.get("total_askp_rsqn"),
        "total_bid_qty": rec.get("total_bidp_rsqn"),
        "levels": levels,
    }


def normalise_tick_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Project a H0STCNT0 record to the fields the UI actually uses."""
    return {
        "ts": rec.get("stck_cntg_hour"),
        "price": rec.get("stck_prpr"),
        "change": rec.get("prdy_vrss"),
        "change_sign": rec.get("prdy_vrss_sign"),
        "change_pct": rec.get("prdy_ctrt"),
        "open": rec.get("stck_oprc"),
        "high": rec.get("stck_hgpr"),
        "low": rec.get("stck_lwpr"),
        "ask1": rec.get("askp1"),
        "bid1": rec.get("bidp1"),
        "qty": rec.get("cntg_vol"),
        "acc_vol": rec.get("acml_vol"),
        "acc_value": rec.get("acml_tr_pbmn"),
        "strength": rec.get("cttr"),          # 체결강도
        "buy_ratio": rec.get("shnu_rate"),
        "direction": rec.get("ccld_dvsn"),    # 1 매수 / 3 매도 / 5 장전
    }


# --------------------------------------------------------------------------- #
# WebSocket client                                                            #
# --------------------------------------------------------------------------- #


class KISWebSocketClient:
    """Single-connection multiplexing client.

    Opens one WebSocket to KIS, then lets callers subscribe/unsubscribe to
    ``(tr_id, symbol)`` pairs. Incoming frames are pushed to a shared
    asyncio.Queue so multiple SSE endpoints (orderbook + ticks) can fan out.

    This client does NOT own reconnection — callers use the ``stream`` helper
    to get an auto-reconnecting async iterator.
    """

    def __init__(
        self,
        *,
        approval_key: str,
        ws_url: str = WS_URL_REAL,
    ) -> None:
        self.approval_key = approval_key
        self.ws_url = ws_url
        self._ws: Any | None = None
        self._subs: set[tuple[str, str]] = set()

    async def __aenter__(self) -> "KISWebSocketClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self.ws_url,
            ping_interval=None,  # KIS sends its own PINGPONG
            close_timeout=5.0,
        )
        logger.info("KIS WS connected: %s", self.ws_url)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover
                pass
            self._ws = None

    async def subscribe(self, tr_id: str, symbol: str) -> None:
        await self._send_sub(tr_id, symbol, tr_type="1")
        self._subs.add((tr_id, symbol))

    async def unsubscribe(self, tr_id: str, symbol: str) -> None:
        await self._send_sub(tr_id, symbol, tr_type="2")
        self._subs.discard((tr_id, symbol))

    async def _send_sub(self, tr_id: str, symbol: str, *, tr_type: str) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        envelope = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": str(symbol).zfill(6)}},
        }
        await self._ws.send(json.dumps(envelope, ensure_ascii=False))
        logger.debug("KIS WS %s %s %s", "sub" if tr_type == "1" else "unsub", tr_id, symbol)

    async def iter_frames(self) -> AsyncIterator[KISFrame]:
        """Yield parsed frames until connection closes."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        try:
            async for msg in self._ws:
                if isinstance(msg, (bytes, bytearray)):
                    msg = msg.decode("utf-8", errors="replace")
                frame = parse_frame(msg)
                # Auto-respond to PINGPONG to keep the connection alive
                if frame.kind == "pingpong":
                    try:
                        await self._ws.send(msg)
                    except Exception:  # pragma: no cover
                        pass
                yield frame
        except ConnectionClosed as exc:
            logger.info("KIS WS closed: code=%s reason=%s", exc.code, exc.reason)
        except WebSocketException as exc:  # pragma: no cover
            logger.warning("KIS WS error: %s", exc)


async def stream_symbol(
    *,
    approval_key: str,
    tr_id: str,
    symbol: str,
    ws_url: str = WS_URL_REAL,
    max_reconnects: int = 5,
) -> AsyncIterator[dict[str, Any]]:
    """High-level helper: open WS, subscribe to ``(tr_id, symbol)``, and yield
    normalised dicts ready for SSE.

    Reconnects with exponential backoff on transient errors.
    """
    attempt = 0
    while True:
        try:
            async with KISWebSocketClient(approval_key=approval_key, ws_url=ws_url) as client:
                await client.subscribe(tr_id, symbol)
                attempt = 0  # reset on successful connect
                async for frame in client.iter_frames():
                    if frame.kind == "data" and frame.records:
                        for rec in frame.records:
                            if tr_id == TR_ORDERBOOK:
                                yield {"type": "orderbook", "symbol": symbol,
                                       "data": normalise_orderbook_record(rec)}
                            elif tr_id == TR_TICK:
                                yield {"type": "tick", "symbol": symbol,
                                       "data": normalise_tick_record(rec)}
                    elif frame.kind == "control":
                        yield {"type": "control", "symbol": symbol,
                               "data": frame.control or {}}
                    elif frame.kind == "error":
                        yield {"type": "error", "symbol": symbol,
                               "data": {"raw": frame.raw[:400]}}
        except (ConnectionClosed, WebSocketException, OSError) as exc:
            logger.warning("KIS WS stream error (%s %s): %s", tr_id, symbol, exc)
        attempt += 1
        if attempt > max_reconnects:
            logger.error("KIS WS stream giving up after %d reconnects", max_reconnects)
            break
        backoff = min(30.0, 1.5 * (2 ** (attempt - 1)))
        await asyncio.sleep(backoff)


__all__ = [
    "WS_URL_REAL",
    "WS_URL_DEMO",
    "TR_ORDERBOOK",
    "TR_TICK",
    "KISFrame",
    "KISWebSocketClient",
    "issue_approval_key",
    "parse_frame",
    "normalise_orderbook_record",
    "normalise_tick_record",
    "stream_symbol",
]
