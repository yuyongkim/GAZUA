"""Sector strength scoreboard.

Computes per-sector:
  - member count
  - mean RS (6-month return percentile of members)
  - aggregate 1D/1W/1M/3M/YTD performance (equal-weighted mean of member returns)
  - sparkline (last 60 trading days, mean normalized)

No pandas; plain Python over the shared :class:`Panel`.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any

from ky_core.scanning.loader import Panel, load_panel


@dataclass
class SectorStrength:
    rank: int
    name: str
    members: int
    score: float           # 0..1 composite; mean RS percentile / 100
    d1: float              # 1-day mean pct
    d5: float              # 5-day mean pct
    m1: float              # 21-day mean pct
    m3: float              # 63-day mean pct
    ytd: float             # year-to-date mean pct
    sparkline: list[float] # last 60 days, equal-weighted normalized index


def sector_strength(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
    top_n: int | None = None,
) -> list[SectorStrength]:
    """Return sectors sorted by composite strength score, descending."""
    panel = panel or load_panel(as_of)
    members_by_sector: dict[str, list[str]] = {}
    for sym, meta in panel.universe.items():
        members_by_sector.setdefault(meta["sector"], []).append(sym)

    results: list[SectorStrength] = []
    for sector, members in members_by_sector.items():
        if len(members) < 3:
            continue  # ignore tiny buckets (data-quality artefacts)
        d1, d5, m1, m3, ytd, rs = _sector_metrics(members, panel)
        spark = _sector_sparkline(members, panel, lookback=60)
        results.append(
            SectorStrength(
                rank=0,
                name=sector,
                members=len(members),
                score=rs,
                d1=d1,
                d5=d5,
                m1=m1,
                m3=m3,
                ytd=ytd,
                sparkline=spark,
            )
        )
    results.sort(key=lambda s: s.score, reverse=True)
    for i, s in enumerate(results, start=1):
        s.rank = i
    if top_n is not None:
        results = results[:top_n]
    return results


def to_dict(ss: SectorStrength) -> dict[str, Any]:
    return asdict(ss)


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


def _pct_return(closes: list[float], back: int) -> float:
    if len(closes) <= back or closes[-back - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-back - 1] - 1.0


def _ytd_return(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    year = rows[-1]["date"][:4]
    first = next((r["close"] for r in rows if r["date"].startswith(year) and r["close"] > 0), None)
    if first is None or first <= 0:
        return 0.0
    return rows[-1]["close"] / first - 1.0


def _sector_metrics(
    members: list[str],
    panel: Panel,
) -> tuple[float, float, float, float, float, float]:
    d1s, d5s, m1s, m3s, ytds, rss = [], [], [], [], [], []
    for sym in members:
        rows = panel.series.get(sym)
        if not rows or len(rows) < 30:
            continue
        closes = [r["close"] for r in rows]
        d1s.append(_pct_return(closes, 1))
        d5s.append(_pct_return(closes, 5))
        m1s.append(_pct_return(closes, 21))
        if len(closes) >= 64:
            m3s.append(_pct_return(closes, 63))
        ytds.append(_ytd_return(rows))
        if len(closes) >= 127:
            rss.append(_pct_return(closes, 126))
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0
    # RS score: percentile of mean 6-month return, shifted to 0..1 via tanh
    # (we don't need the cross-sector percentile: the caller sorts by score)
    rs_raw = _mean(rss)
    score = max(0.0, min(1.0, 0.5 + rs_raw))  # +50% => 1.0 ; -50% => 0.0
    return _mean(d1s), _mean(d5s), _mean(m1s), _mean(m3s), _mean(ytds), score


def _sector_sparkline(
    members: list[str],
    panel: Panel,
    *,
    lookback: int,
) -> list[float]:
    # Build equal-weighted cumulative index over last `lookback` days.
    # Align on last `lookback` entries of each member that has enough data.
    series: list[list[float]] = []
    for sym in members:
        rows = panel.series.get(sym)
        if not rows or len(rows) < lookback:
            continue
        closes = [r["close"] for r in rows[-lookback:]]
        if closes[0] <= 0:
            continue
        series.append([c / closes[0] for c in closes])
    if not series:
        return []
    out: list[float] = []
    for i in range(lookback):
        vals = [s[i] for s in series if i < len(s)]
        if not vals:
            out.append(1.0)
        else:
            out.append(sum(vals) / len(vals))
    return out
