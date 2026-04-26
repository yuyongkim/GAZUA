"""Sector lead-lag network — 34섹터 페어별 cross-correlation 분석.

각 섹터의 macro strength time-series (z-score)를 추출하여 페어별 CCF 계산.
양의 lag k에서 max correlation → 섹터 A가 섹터 B를 k개월 선행.

Output:
  - pair-level: (sec_a, sec_b, best_lag, corr) 매트릭스
  - sector-level: 섹터별 net lead score (다른 섹터 대비 평균 선행 lag)
"""
from __future__ import annotations

import csv
import logging
import re
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_ROOT = Path.home() / ".gazua" / "data" / "sectors"
MAP_PATH = (
    Path(__file__).resolve().parents[4] / "docs" / "SECTOR_INDICATOR_MAP.md"
)


# Reuse parser from macro_sector_strength
from ky_core.scoring.macro_sector_strength import (  # noqa: E402
    _parse_map,
    _disk_index,
    _resolve_cell,
    _read_values,
    _yoy_change,
)


@dataclass
class SectorPairCorr:
    sector_a: str
    sector_a_name: str
    sector_b: str
    sector_b_name: str
    best_lag: int           # months — positive: A leads B
    corr: float             # at best_lag
    n_overlap: int


@dataclass
class SectorLeadScore:
    sector_num: str
    sector_name: str
    avg_lead_lag: float    # mean lag (positive = leading)
    n_pairs: int
    top_followers: list[dict[str, Any]]  # 3 sectors that A leads
    top_leaders: list[dict[str, Any]]    # 3 sectors that lead A


def _build_sector_series(
    method: str = "yoy",
    max_lag_months: int = 12,
) -> dict[str, list[float]]:
    """각 sector의 매크로 신호 시계열 (월별 평균 z-score).

    Returns: {sector_num: [monthly z-score series]}
    """
    cells = _parse_map()
    csvs = _disk_index()

    by_sector: dict[str, list[list[float]]] = {}
    for c in cells:
        match = _resolve_cell(c, csvs)
        if not match:
            continue
        folder, slug = match
        values = _read_values(folder, slug)
        if not values or len(values) < 36:
            continue
        if method == "yoy":
            series = _yoy_change(values)
        else:
            series = values
        if len(series) < 36:
            continue
        # Normalize length: take last 360 (~30년 월별 / 8년 일별)
        # 모든 시계열은 raw 길이로 두고, 페어 비교 시 짧은 쪽 기준 align
        by_sector.setdefault(c["sector_num"], []).append(series)

    # Per-sector aggregate: mean across cells (length-normalized)
    out: dict[str, list[float]] = {}
    for sec, multi in by_sector.items():
        # Find minimum length
        min_len = min(len(s) for s in multi)
        # Cap to last 360
        cap = min(min_len, 360)
        aligned = [s[-cap:] for s in multi]
        # Mean across cells per timestep
        avg = []
        for t in range(cap):
            vals = [s[t] for s in aligned if t < len(s)]
            avg.append(sum(vals) / len(vals) if vals else 0.0)
        out[sec] = avg
    return out


def _ccf(a: list[float], b: list[float], max_lag: int) -> tuple[int, float]:
    """Cross-correlation function. Returns (best_lag, max_corr_abs).
    positive lag k: a[t-k] vs b[t] → a leads b.
    """
    if len(a) < max_lag + 12 or len(b) < max_lag + 12:
        return 0, 0.0
    n = min(len(a), len(b))
    a = a[-n:]
    b = b[-n:]
    best_lag = 0
    best_corr = 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x = a[: n - lag]
            y = b[lag:]
        else:
            x = a[-lag:]
            y = b[: n + lag]
        if len(x) < 12 or len(x) != len(y):
            continue
        try:
            corr = statistics.correlation(x, y)
        except (statistics.StatisticsError, ValueError):
            continue
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag
    return best_lag, best_corr


def compute_pair_correlations(
    method: str = "yoy",
    max_lag_months: int = 12,
    min_corr: float = 0.3,
) -> list[SectorPairCorr]:
    """모든 sector 페어 (~561개) 의 lead-lag CCF 계산.
    min_corr 이상인 페어만 반환.
    """
    sec_series = _build_sector_series(method=method)
    sec_names = {c["sector_num"]: c["sector_name"] for c in _parse_map()}
    secs = sorted(sec_series.keys())

    out: list[SectorPairCorr] = []
    for i, a in enumerate(secs):
        for b in secs[i + 1:]:
            sa, sb = sec_series[a], sec_series[b]
            if not sa or not sb:
                continue
            n = min(len(sa), len(sb))
            best_lag, corr = _ccf(sa, sb, max_lag_months)
            if abs(corr) < min_corr:
                continue
            out.append(SectorPairCorr(
                sector_a=a, sector_a_name=sec_names.get(a, "?"),
                sector_b=b, sector_b_name=sec_names.get(b, "?"),
                best_lag=best_lag, corr=round(corr, 3),
                n_overlap=n,
            ))
    out.sort(key=lambda x: abs(x.corr), reverse=True)
    return out


def compute_sector_lead_scores(
    pairs: list[SectorPairCorr] | None = None,
    method: str = "yoy",
) -> list[SectorLeadScore]:
    """각 섹터의 net lead score = mean(best_lag for pairs where this sector is sec_a) - mean(... sec_b).
    양수 클수록 평균적으로 다른 섹터 선행.
    """
    pairs = pairs or compute_pair_correlations(method=method, min_corr=0.3)
    sec_names = {c["sector_num"]: c["sector_name"] for c in _parse_map()}

    # leads: sector → list of (other, lag) where this sector leads
    leads: dict[str, list[tuple[str, int, float]]] = {}
    follows: dict[str, list[tuple[str, int, float]]] = {}
    for p in pairs:
        if p.best_lag > 0:
            # a leads b by best_lag
            leads.setdefault(p.sector_a, []).append((p.sector_b, p.best_lag, p.corr))
            follows.setdefault(p.sector_b, []).append((p.sector_a, p.best_lag, p.corr))
        elif p.best_lag < 0:
            leads.setdefault(p.sector_b, []).append((p.sector_a, -p.best_lag, p.corr))
            follows.setdefault(p.sector_a, []).append((p.sector_b, -p.best_lag, p.corr))
        # lag 0: 동행, skip

    out: list[SectorLeadScore] = []
    for sec, name in sec_names.items():
        led = leads.get(sec, [])
        flw = follows.get(sec, [])
        n_pairs = len(led) + len(flw)
        if n_pairs == 0:
            avg = 0.0
        else:
            lead_sum = sum(lag for _, lag, _ in led)
            follow_sum = sum(lag for _, lag, _ in flw)
            avg = (lead_sum - follow_sum) / n_pairs

        top_followers = sorted(led, key=lambda x: x[1], reverse=True)[:3]
        top_leaders = sorted(flw, key=lambda x: x[1], reverse=True)[:3]
        out.append(SectorLeadScore(
            sector_num=sec,
            sector_name=name,
            avg_lead_lag=round(avg, 2),
            n_pairs=n_pairs,
            top_followers=[
                {"sector_num": s, "sector_name": sec_names.get(s, "?"),
                 "lag": lag, "corr": round(corr, 3)}
                for s, lag, corr in top_followers
            ],
            top_leaders=[
                {"sector_num": s, "sector_name": sec_names.get(s, "?"),
                 "lag": lag, "corr": round(corr, 3)}
                for s, lag, corr in top_leaders
            ],
        ))
    out.sort(key=lambda x: x.avg_lead_lag, reverse=True)
    return out
