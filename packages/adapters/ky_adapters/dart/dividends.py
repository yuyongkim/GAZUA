"""DART dividend-history extractor.

Path-of-least-resistance: OpenDART ships a dedicated structured endpoint
``/api/alotMatter.json`` (배당에 관한 사항) that returns the filing's
dividend table. Each annual 사업보고서 has one call returning rows like::

    {
      "rcept_no": "20240315000123",
      "corp_code": "00126380",
      "se": "유동비율(%)",            # row kind
      "stock_knd": "보통주",          # 보통주 / 우선주
      "thstrm": "1,000",              # 당기 (KRW per share for 주당 배당금)
      "frmtrm": "900",                # 전기
      "lwfr": "800",                  # 전전기
      ...
    }

Interesting ``se`` values for us:

- ``"주당 현금배당금(원)"``  → DPS (common or preferred)
- ``"현금배당금총액"``        → total payout (KRW)
- ``"현금배당성향(%)"``       → payout ratio
- ``"현금배당수익률(%)"``     → dividend yield at fiscal-year-end

The endpoint covers the current filing's fiscal year + previous two. Walking a
few annual reports → 6-10 years of DPS history per symbol, which is enough to
decide aristocrat candidacy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter
from ky_adapters.dart.client import DART_BASE, REPORT_CODES

log = logging.getLogger(__name__)


@dataclass
class DividendYear:
    symbol: str
    corp_code: str
    period_end: str          # ISO YYYY-12-31
    share_type: str          # "common" | "preferred"
    dps: float | None = None
    payout_total: float | None = None
    payout_ratio: float | None = None
    dividend_yield: float | None = None
    source_id: str = "dart"

    def as_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "corp_code": self.corp_code,
            "period_end": self.period_end,
            "share_type": self.share_type,
            "dps": self.dps,
            "payout_total": self.payout_total,
            "payout_ratio": self.payout_ratio,
            "dividend_yield": self.dividend_yield,
            "source_id": self.source_id,
        }


# ``se`` → our field key. Substring match — DART varies whitespace.
_FIELD_MAP = {
    "주당 현금배당금": "dps",
    "주당현금배당금": "dps",
    "현금배당금총액": "payout_total",
    "현금배당성향": "payout_ratio",
    "현금배당수익률": "dividend_yield",
}


def _classify_share(name: str) -> str:
    n = (name or "").strip()
    if "우선" in n:
        return "preferred"
    return "common"


def _parse_num(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("원", "").replace("%", "").strip()
    if not text or text in ("-", "–", "—", "N/A"):
        return None
    neg = False
    if text.startswith(("△", "▽", "(")) or text.endswith(")"):
        neg = True
        text = text.strip("△▽() ")
    try:
        v = float(text)
    except ValueError:
        return None
    return -v if neg else v


def _field_key(se: str) -> str | None:
    s = (se or "").strip()
    for marker, key in _FIELD_MAP.items():
        if marker in s:
            return key
    return None


class DARTDividendExtractor(BaseAdapter):
    source_id = "dart_dividends"
    priority = 10

    def __init__(self, api_key: Optional[str] = None, base_url: str = DART_BASE) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "DARTDividendExtractor":
        return cls(api_key=cls._env("DART_API_KEY"))

    def healthcheck(self) -> dict[str, Any]:
        if not self.api_key:
            return self._timed_fail(self.source_id, "DART_API_KEY not configured")
        return self._timed_ok(0.0, self.source_id, {"configured": True})

    def fetch_year(
        self,
        corp_code: str,
        year: int,
        report_code: str = REPORT_CODES["annual"],
    ) -> list[dict[str, Any]]:
        """Raw ``alotMatter.json`` rows for a single (corp, year) tuple."""
        if not self.api_key:
            raise AuthError("DART_API_KEY not configured")
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
        }
        resp = self._request("GET", f"{self.base_url}/alotMatter.json", params=params)
        if resp.status_code != 200:
            raise AdapterError(f"DART alotMatter → HTTP {resp.status_code}")
        body = resp.json()
        status = body.get("status")
        if status == "013":  # "조회된 데이터가 없습니다"
            return []
        if status != "000":
            raise AdapterError(f"DART alotMatter status={status}: {body.get('message')}")
        return body.get("list", []) or []

    def extract_history(
        self,
        symbol: str,
        corp_code: str,
        years: Iterable[int] | None = None,
    ) -> list[DividendYear]:
        """Walk multiple fiscal years and roll up per-year DPS history.

        Each annual filing returns up to three fiscal years (current / prev /
        prev-prev). We call every 3rd year to cover the range efficiently.
        """
        if years is None:
            this = date.today().year
            # Fetch annual reports for the most recent 4 completed fiscal years.
            # Each gives 3y of history → 10+ years overlap-deduped.
            years = [this - 1, this - 4, this - 7, this - 10]

        by_year: dict[tuple[str, str], DividendYear] = {}
        for y in years:
            try:
                rows = self.fetch_year(corp_code, y)
            except AdapterError as exc:
                log.debug("dividend fetch %s@%s failed: %s", symbol, y, exc)
                continue
            # Each row carries thstrm (current), frmtrm (prior), lwfr (prior-prev)
            # keyed by ``se`` (field) and ``stock_knd`` (share kind).
            for row in rows:
                se_key = _field_key(row.get("se", ""))
                if not se_key:
                    continue
                share_type = _classify_share(row.get("stock_knd") or "")
                triples = (
                    (f"{y}-12-31", _parse_num(row.get("thstrm"))),
                    (f"{y-1}-12-31", _parse_num(row.get("frmtrm"))),
                    (f"{y-2}-12-31", _parse_num(row.get("lwfr"))),
                )
                for period_end, val in triples:
                    if val is None:
                        continue
                    key = (period_end, share_type)
                    ent = by_year.get(key)
                    if ent is None:
                        ent = DividendYear(
                            symbol=symbol,
                            corp_code=corp_code,
                            period_end=period_end,
                            share_type=share_type,
                        )
                        by_year[key] = ent
                    # Merge the field; keep the first non-None (filings often
                    # repeat the same value across subsequent years).
                    current = getattr(ent, se_key)
                    if current is None:
                        setattr(ent, se_key, val)

        return sorted(
            by_year.values(),
            key=lambda r: (r.share_type, r.period_end),
        )
