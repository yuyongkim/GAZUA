"""EXIM (Korea Eximbank) exchange rate adapter.

Endpoint:
  https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON
Query:
  ?authkey=...&searchdate=YYYYMMDD&data=AP01

Response envelope codes (single-row ``result`` field when the request failed):
  1 = OK
  2 = DATA CODE ERROR (wrong ``data=`` value)
  3 = AUTH KEY INVALID / EXPIRED  (also triggered when the issuing IP is not
      whitelisted on the Eximbank portal)
  4 = ACCESS DENIED / REVOKED

Status as of 2026-04-22
-----------------------
EXIM auth is **currently broken** (``result=3`` returns for every valid key we
have tried, including a freshly issued one, because the Eximbank portal does
IP-whitelisting that ky-platform does not participate in). Until we can
onboard a whitelisted issuing IP, every ``healthcheck`` / ``get_rates`` call
raises :class:`AuthError`.

Downstream code does **not** break: the macro compass, the collector, and the
picker resolve USDKRW through the fallback chain defined in
:data:`ky_core.storage.presets.MACRO_TIER_A["USDKRW"]`::

    "USDKRW": [
        ("ecos", "731Y001", "0000001", "D"),   # primary — BoK 원/달러 매매기준율
        ("fred", "DEXKOUS"),                   # secondary — Fed KRW/USD noon rate
        ("exim",),                              # tertiary — only when the other two are dry
    ]

Both primary and secondary series are already collected daily (see
``FRED_SERIES`` and ``ECOS_SERIES`` in :mod:`ky_core.storage.presets`), so the
auth failure here is **informational**, not a blocker. The
:class:`ky_core.macro.pickers.pick_indicator` call surfaces
``fallback_rank > 0`` so operators can see when the primary feed goes dry.

Operators who want to re-enable EXIM:

1. Re-issue the EXIM key at https://www.koreaexim.go.kr (Open API console).
2. Whitelist the issuing IP on the Eximbank portal.
3. Set ``EXIM_API_KEY`` in ``.env`` and run ``scripts/collect.py --source exim``.
4. Verify via ``EXIMAdapter.from_settings().healthcheck()`` — ``ok=True``.

Until then we keep this adapter in-tree because (a) the scaffolding is
useful when auth is fixed and (b) every EXIM call goes through ``AuthError``
with a pointer to the fallback chain above.

Note: the Eximbank only publishes on business days and serves the most recent
available rate. Querying a weekend/holiday returns an empty list — callers
should fall back to the prior business day.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter

EXIM_BASE = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"


@dataclass
class EXIMRate:
    cur_unit: str  # e.g. USD, JPY(100), CNH
    cur_nm: str
    deal_bas_r: float | None  # 매매기준율
    ttb: float | None  # 전신환 매입률
    tts: float | None  # 전신환 매도율
    as_of: str  # ISO YYYY-MM-DD

    def as_row(self, source_id: str = "exim") -> dict[str, Any]:
        series_id = f"fx/{self.cur_unit}/deal_bas_r"
        return {
            "source_id": source_id,
            "series_id": series_id,
            "date": self.as_of,
            "value": self.deal_bas_r,
            "unit": "KRW per unit",
        }


class EXIMAdapter(BaseAdapter):
    source_id = "exim"
    priority = 2

    def __init__(self, auth_key: Optional[str] = None, base_url: str = EXIM_BASE) -> None:
        super().__init__()
        self.auth_key = auth_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "EXIMAdapter":
        return cls(auth_key=cls._env("EXIM_API_KEY"))

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        if not self.auth_key:
            return self._timed_fail(self.source_id, "EXIM_API_KEY not configured")
        t0 = time.perf_counter()
        try:
            # probe a recent weekday (today may be a weekend → empty but valid)
            from datetime import timedelta
            probe_day = date.today()
            # back up to the previous weekday
            while probe_day.weekday() >= 5:
                probe_day -= timedelta(days=1)
            rates = self.get_rates(search_date=probe_day)
            ok = isinstance(rates, list) and len(rates) > 0
        except AuthError as exc:
            return self._timed_fail(
                self.source_id,
                f"{exc} — fall back to ECOS 731Y001 / FRED DEXKOUS",
            )
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"api_ok": ok, "rows": len(rates)})

    # --------- API surface ---------

    def get_rates(
        self,
        search_date: date | str | None = None,
        data: str = "AP01",
    ) -> list[EXIMRate]:
        """Fetch FX rates. ``data=AP01`` is the standard FX feed.

        Raises :class:`AuthError` on ``result=3``/``result=4`` (key invalid,
        expired, or IP not whitelisted) so the runner can fall back to the
        ECOS/FRED FX sources cleanly.
        """
        if not self.auth_key:
            raise AuthError("EXIM_API_KEY not configured")
        iso = _eximdate(search_date) if search_date else ""
        params: dict[str, Any] = {"authkey": self.auth_key, "data": data}
        if iso:
            params["searchdate"] = iso
        resp = self._request("GET", self.base_url, params=params)
        if resp.status_code != 200:
            raise AdapterError(f"EXIM → HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"EXIM: invalid JSON: {exc}") from exc

        # EXIM quirk: auth failures come back as a single-row JSON array with
        # ``result`` set and every numeric field None — not as a dict envelope.
        # Detect both shapes so we don't silently upsert garbage.
        envelope = None
        if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict):
            row = body[0]
            # when result == 3/4 the row is all-nulls except result
            if "result" in row and row.get("cur_unit") in (None, ""):
                envelope = row
        elif isinstance(body, dict) and "result" in body:
            envelope = body

        if envelope is not None:
            code = envelope.get("result")
            if code in (3, 4):
                raise AuthError(
                    f"EXIM result={code}: authentication key invalid/expired "
                    "or issuing IP not whitelisted. Use ECOS 731Y001/0000001 "
                    "or FRED DEXKOUS as the KRW/USD fallback."
                )
            if code == 2:
                raise AdapterError(f"EXIM result=2: DATA code invalid ({data!r})")
            if code and code != 1:
                raise AdapterError(f"EXIM result={code}")
            # result==1 with empty row — holiday/weekend, fall through to []

        if not isinstance(body, list):
            return []
        iso_out = _iso_from_eximdate(iso) if iso else date.today().isoformat()
        out: list[EXIMRate] = []
        for row in body:
            if not isinstance(row, dict):
                continue
            # skip the all-null envelope row
            if row.get("cur_unit") in (None, "") and row.get("deal_bas_r") in (None, ""):
                continue
            out.append(
                EXIMRate(
                    cur_unit=row.get("cur_unit", ""),
                    cur_nm=row.get("cur_nm", ""),
                    deal_bas_r=_parse_num(row.get("deal_bas_r")),
                    ttb=_parse_num(row.get("ttb")),
                    tts=_parse_num(row.get("tts")),
                    as_of=iso_out,
                )
            )
        return out


def _parse_num(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", ""))
    except ValueError:
        return None


def _eximdate(d: date | str) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    raw = str(d).replace("-", "")
    return raw


def _iso_from_eximdate(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return yyyymmdd
