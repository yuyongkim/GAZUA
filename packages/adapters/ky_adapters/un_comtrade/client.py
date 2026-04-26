"""UN Comtrade adapter (free public tier).

Endpoint: https://comtradeapi.un.org/public/v1/preview/<typeCode>/<freqCode>/<clCode>

The free public preview tier serves the most recent ~12 months without an
API key. For deeper history register at https://comtradeapi.un.org/ and set
``COMTRADE_API_KEY`` (subscription-key header).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

COMTRADE_BASE = "https://comtradeapi.un.org/public/v1/preview"


@dataclass
class ComtradeRow:
    period: str
    reporter_iso3: str
    partner_iso3: str
    hs_code: str
    flow: str           # "X" export, "M" import
    trade_value_usd: Optional[float]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "un_comtrade") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "period": self.period,
            "reporter_iso3": self.reporter_iso3,
            "partner_iso3": self.partner_iso3,
            "hs_code": self.hs_code,
            "flow": self.flow,
            "trade_value_usd": self.trade_value_usd,
        }


class UNComtradeAdapter(BaseAdapter):
    source_id = "un_comtrade"
    priority = 5

    def __init__(self, base_url: str = COMTRADE_BASE, api_key: Optional[str] = None) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key

    @classmethod
    def from_settings(cls) -> "UNComtradeAdapter":
        return cls(api_key=cls._env("COMTRADE_API_KEY"))

    def healthcheck(self) -> dict[str, Any]:
        from datetime import date
        t0 = time.perf_counter()
        # Comtrade lag is ~3-6 months on the public preview; use 6-month-old.
        today = date.today()
        ym = today.year * 12 + today.month - 6
        recent_period = f"{ym // 12:04d}{(ym % 12) + 1:02d}"
        try:
            rows = self.get_trade(
                reporter_iso3="KOR",
                partner_iso3="WLD",
                hs_code="270900",
                period=recent_period,
                flow="M",
            )
            ok = isinstance(rows, list)
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(
            latency_ms, self.source_id,
            {"sample_rows": len(rows), "probed_period": recent_period},
        )

    def get_trade_history(
        self,
        reporter_iso3: str = "KOR",
        partner_iso3: str = "WLD",
        hs_code: str = "TOTAL",
        flow: str = "X",
        year_start: int = 1995,
        year_end: int = 2024,
    ) -> list[ComtradeRow]:
        """Annual time series 1995~2024. Comma-joins years into a single
        Comtrade call (preview tier supports it). Falls back to per-year
        loop on error so we always get partial coverage."""
        period = ",".join(str(y) for y in range(year_start, year_end + 1))
        try:
            return self.get_trade(
                reporter_iso3=reporter_iso3,
                partner_iso3=partner_iso3,
                hs_code=hs_code,
                period=period,
                flow=flow,
                freq="A",
            )
        except Exception:
            out: list[ComtradeRow] = []
            for y in range(year_start, year_end + 1):
                try:
                    rows = self.get_trade(
                        reporter_iso3=reporter_iso3,
                        partner_iso3=partner_iso3,
                        hs_code=hs_code,
                        period=str(y),
                        flow=flow,
                        freq="A",
                    )
                    out.extend(rows)
                except Exception:
                    continue
            return out

    def get_trade(
        self,
        reporter_iso3: str = "KOR",
        partner_iso3: str = "WLD",
        hs_code: str = "TOTAL",
        period: str = "latest",
        flow: str = "X",     # X export, M import
        type_code: str = "C",  # C goods, S services
        freq: str = "M",       # M monthly, A annual
    ) -> list[ComtradeRow]:
        url = f"{self.base_url}/{type_code}/{freq}/HS"
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Ocp-Apim-Subscription-Key"] = self.api_key
        params: dict[str, Any] = {
            "reporterCode": _iso3_to_m49(reporter_iso3),
            "partnerCode": _iso3_to_m49(partner_iso3),
            "cmdCode": hs_code,
            "flowCode": flow,
            "period": period,
            "format": "JSON",
        }
        # Strip None entries (helpful when a code isn't found)
        params = {k: v for k, v in params.items() if v is not None}
        resp = self._request("GET", url, params=params, headers=headers)
        if resp.status_code != 200:
            raise AdapterError(
                f"UN Comtrade → HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"UN Comtrade response not JSON: {exc}") from exc
        rows_raw = payload.get("data") or payload.get("dataset") or []
        out: list[ComtradeRow] = []
        for r in rows_raw:
            out.append(
                ComtradeRow(
                    period=str(r.get("period") or r.get("periodId") or ""),
                    reporter_iso3=str(r.get("reporterISO") or reporter_iso3),
                    partner_iso3=str(r.get("partnerISO") or partner_iso3),
                    hs_code=str(r.get("cmdCode") or hs_code),
                    flow=str(r.get("flowCode") or flow),
                    trade_value_usd=_to_float(r.get("primaryValue") or r.get("TradeValue")),
                    raw=r,
                )
            )
        return out


# UN Comtrade uses M49 country codes, not ISO3. Tiny lookup for the few we
# care about; expand as needed.
_M49 = {
    "KOR": "410", "USA": "842", "CHN": "156", "JPN": "392", "DEU": "276",
    "VNM": "704", "TWN": "158", "IND": "356", "MEX": "484", "GBR": "826",
    "FRA": "250", "ITA": "380", "ESP": "724", "AUS": "036", "RUS": "643",
    "WLD": "0",   # all-world
}


def _iso3_to_m49(iso: str) -> str:
    return _M49.get(iso.upper(), iso)


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
