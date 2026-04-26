"""Google Trends adapter — search interest as a sentiment leading indicator.

Wraps the `pytrends` library so the rest of ky-platform sees the standard
adapter contract. pytrends is rate-limited and occasionally blocks us; the
adapter raises AdapterError instead of letting the underlying exception
escape.

Note on accuracy: Google returns relative search interest (0-100 normalised),
not absolute query volume. Useful for *direction*, not levels.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter


@dataclass
class TrendsObservation:
    keyword: str
    geo: str
    period: str
    value: Optional[float]

    def as_row(self, source_id: str = "pytrends") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "keyword": self.keyword,
            "geo": self.geo,
            "period": self.period,
            "value": self.value,
        }


_PATCHED = False


def _patch_urllib3_for_pytrends() -> None:
    """pytrends 4.9.x calls urllib3.util.retry.Retry(method_whitelist=...) which
    was renamed to ``allowed_methods`` in urllib3>=1.26 and removed in 2.x.
    We translate the kwarg before pytrends imports."""
    global _PATCHED
    if _PATCHED:
        return
    try:
        from urllib3.util import retry as _retry  # noqa: WPS433
    except ImportError:
        return
    orig_init = _retry.Retry.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "method_whitelist" in kwargs and "allowed_methods" not in kwargs:
            kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
        return orig_init(self, *args, **kwargs)

    _retry.Retry.__init__ = patched_init  # type: ignore[method-assign]
    _PATCHED = True


def _import_pytrends():
    _patch_urllib3_for_pytrends()
    try:
        from pytrends.request import TrendReq  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover — optional dep
        raise AdapterError(
            "pytrends not installed. `pip install pytrends` to enable this adapter."
        ) from exc
    return TrendReq


class PyTrendsAdapter(BaseAdapter):
    source_id = "pytrends"
    priority = 4

    def __init__(self, hl: str = "ko-KR", tz: int = 540) -> None:
        # tz=540 → KST (minutes from UTC)
        super().__init__()
        self.hl = hl
        self.tz = tz

    @classmethod
    def from_settings(cls) -> "PyTrendsAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.interest_over_time(["반도체"], geo="KR", timeframe="today 3-m")
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def interest_over_time(
        self,
        keywords: list[str],
        geo: str = "KR",
        timeframe: str = "today 12-m",
    ) -> list[TrendsObservation]:
        """`timeframe` examples: 'today 12-m', 'today 3-m', 'now 7-d', '2024-01-01 2024-12-31'.
        `geo`: '' = global, 'KR' = Korea, 'US', etc."""
        TrendReq = _import_pytrends()
        try:
            client = TrendReq(hl=self.hl, tz=self.tz, retries=2, backoff_factor=0.5)
            client.build_payload(keywords, timeframe=timeframe, geo=geo)
            df = client.interest_over_time()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"pytrends call failed: {exc}") from exc
        if df is None or df.empty:
            return []
        out: list[TrendsObservation] = []
        for ts, row in df.iterrows():
            for kw in keywords:
                if kw not in row:
                    continue
                out.append(
                    TrendsObservation(
                        keyword=kw,
                        geo=geo or "GLOBAL",
                        period=ts.strftime("%Y-%m-%d"),
                        value=float(row[kw]) if row[kw] is not None else None,
                    )
                )
        return out
