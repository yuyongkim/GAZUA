"""openFDA adapter — daily / monthly drug-approval counts.

Free public API at api.fda.gov. No key needed for low-volume calls
(≤240/min, 1000/hour anonymous).

Series we expose:
  - daily approval counts (15K rows, 1939→present)
  - monthly aggregated counts (rollup helper)
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

OPENFDA_BASE = "https://api.fda.gov"


@dataclass
class FDAApprovalRow:
    date: str  # YYYY-MM-DD
    count: int
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "openfda") -> dict[str, Any]:
        return {"source_id": source_id, "date": self.date, "count": self.count}


class OpenFDAAdapter(BaseAdapter):
    source_id = "openfda"
    priority = 6

    @classmethod
    def from_settings(cls) -> "OpenFDAAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.get_drug_approvals_daily()
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def get_drug_approvals_daily(self) -> list[FDAApprovalRow]:
        """Returns daily approval counts from the entire FDA submissions DB."""
        url = f"{OPENFDA_BASE}/drug/drugsfda.json"
        params = {"count": "submissions.submission_status_date"}
        resp = self._request("GET", url, params=params)
        if resp.status_code != 200:
            raise AdapterError(f"openFDA → HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = data.get("results", [])
        out: list[FDAApprovalRow] = []
        for r in results:
            t = r.get("time")
            if not t or len(t) != 8:
                continue
            iso = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
            try:
                count = int(r.get("count", 0))
            except (TypeError, ValueError):
                count = 0
            out.append(FDAApprovalRow(date=iso, count=count, raw=r))
        return out

    def get_drug_approvals_monthly(
        self, start_year: int = 2014
    ) -> list[FDAApprovalRow]:
        """Roll up daily counts into monthly buckets from start_year forward."""
        daily = self.get_drug_approvals_daily()
        bucket: dict[str, int] = defaultdict(int)
        for row in daily:
            year = int(row.date[:4])
            if year < start_year:
                continue
            ym = row.date[:7]  # YYYY-MM
            bucket[ym] += row.count
        return [
            FDAApprovalRow(date=ym, count=cnt)
            for ym, cnt in sorted(bucket.items())
        ]
