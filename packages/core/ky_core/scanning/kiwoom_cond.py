"""Kiwoom condition scanner — 키움 조건식 7종.

Ported from QuantPlatform/analysis/kiwoom_condition_analyzer.py, adapted to
the ky-platform Panel so we evaluate the full KOSPI+KOSDAQ universe in one
pass and return both per-condition counts and the top passing symbols.

7 Conditions (A..G):
    A: MA5  과 MA20 이 2% 이내 근접           (converging short MAs)
    B: MA20 과 MA60 이 2% 이내 근접           (converging mid MAs)
    C: MA20 이 하락·보합 추세 (어제 대비)     (coiled base)
    D: MA5 → MA20 골든크로스                  (short-term GC)
    E: MA5 → MA60 골든크로스                  (mid-term GC)
    F: 당일 거래량이 전일 대비 2배 이상       (volume surge 100%+)
    G: 당일 거래량 >= 100,000주               (liquidity floor)

All seven together is the "원조 조건식" signal; 4 of 7 is the looser
매매 candidate universe.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any, Callable

from ky_core.scanning.loader import Panel, load_panel


CONDITION_META: list[dict[str, str]] = [
    {"id": "A", "name": "MA5/MA20 근접 2%",    "desc": "단기 이평 수렴 — 베이스 완성 임박"},
    {"id": "B", "name": "MA20/MA60 근접 2%",   "desc": "중기 이평 수렴 — 대형 베이스 완성"},
    {"id": "C", "name": "MA20 하락·보합",      "desc": "추세 휴식기 → 새로운 상승 준비"},
    {"id": "D", "name": "MA5 → MA20 GC",       "desc": "단기 골든크로스 발생"},
    {"id": "E", "name": "MA5 → MA60 GC",       "desc": "중기 골든크로스 (강한 전환 신호)"},
    {"id": "F", "name": "거래량 2배 이상",      "desc": "전일 대비 거래량 100%+ 급증"},
    {"id": "G", "name": "거래량 ≥ 100k",        "desc": "기관 관심 가능 최소 유동성"},
]


@dataclass
class CondHit:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    vol: int
    vol_ratio: float
    ma5: float
    ma20: float
    ma60: float
    pct_1d: float
    reason: str


@dataclass
class CondBucket:
    id: str
    name: str
    desc: str
    pass_count: int
    top: list[CondHit]


@dataclass
class KiwoomCondBundle:
    as_of: str
    universe_size: int
    buckets: list[CondBucket]
    intersection_4of7: list[CondHit]
    intersection_all: list[CondHit]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def scan_kiwoom(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
    top_per_bucket: int = 30,
    top_intersection: int = 40,
) -> KiwoomCondBundle:
    panel = panel or load_panel(as_of)

    per_cond: dict[str, list[CondHit]] = {c["id"]: [] for c in CONDITION_META}
    four_of_seven: list[CondHit] = []
    seven_of_seven: list[CondHit] = []

    for sym, rows in panel.series.items():
        meta = panel.universe.get(sym)
        if not meta or len(rows) < 65:
            continue

        closes = [r["close"] for r in rows]
        vols = [int(r.get("volume") or 0) for r in rows]

        ma5 = _sma(closes, 5)
        ma20 = _sma(closes, 20)
        ma60 = _sma(closes, 60)
        ma5_prev = _sma(closes[:-1], 5)
        ma20_prev = _sma(closes[:-1], 20)
        ma60_prev = _sma(closes[:-1], 60)
        ma20_d20 = _sma(closes[:-20], 20) if len(closes) > 25 else ma20

        if ma20 <= 0 or ma60 <= 0:
            continue

        cur_close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else cur_close
        pct_1d = (cur_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
        cur_vol = vols[-1]
        prev_vol = vols[-2] if len(vols) >= 2 else cur_vol
        vol_ratio = (cur_vol / prev_vol) if prev_vol else 0.0

        cond = {
            "A": abs(ma5 - ma20) / ma20 <= 0.02,
            "B": abs(ma20 - ma60) / ma60 <= 0.02,
            "C": ma20 <= ma20_d20,
            "D": (ma5 > ma20) and (ma5_prev <= ma20_prev),
            "E": (ma5 > ma60) and (ma5_prev <= ma60_prev),
            "F": vol_ratio >= 2.0,
            "G": cur_vol >= 100_000,
        }

        passed = [cid for cid, ok in cond.items() if ok]
        if not passed:
            continue

        hit_template = _build_hit(
            sym=sym,
            meta=meta,
            close=cur_close,
            vol=cur_vol,
            vol_ratio=vol_ratio,
            ma5=ma5,
            ma20=ma20,
            ma60=ma60,
            pct_1d=pct_1d,
            passed=passed,
        )
        for cid in passed:
            per_cond[cid].append(hit_template)

        if len(passed) >= 4:
            four_of_seven.append(hit_template)
        if len(passed) == 7:
            seven_of_seven.append(hit_template)

    # Sort each bucket by volume-surge × 1-day move (rough strength).
    def _strength(h: CondHit) -> float:
        return h.vol_ratio * (1.0 + max(h.pct_1d, 0.0) / 10)

    buckets: list[CondBucket] = []
    for meta in CONDITION_META:
        hits = per_cond[meta["id"]]
        hits.sort(key=_strength, reverse=True)
        buckets.append(
            CondBucket(
                id=meta["id"],
                name=meta["name"],
                desc=meta["desc"],
                pass_count=len(hits),
                top=hits[:top_per_bucket],
            )
        )

    four_of_seven.sort(key=_strength, reverse=True)
    seven_of_seven.sort(key=_strength, reverse=True)

    return KiwoomCondBundle(
        as_of=panel.as_of,
        universe_size=len(panel.universe),
        buckets=buckets,
        intersection_4of7=four_of_seven[:top_intersection],
        intersection_all=seven_of_seven[:top_intersection],
    )


def to_dict(b: KiwoomCondBundle) -> dict[str, Any]:
    return {
        "as_of": b.as_of,
        "universe_size": b.universe_size,
        "buckets": [
            {
                "id": buc.id,
                "name": buc.name,
                "desc": buc.desc,
                "pass_count": buc.pass_count,
                "top": [asdict(h) for h in buc.top],
            }
            for buc in b.buckets
        ],
        "intersection_4of7": [asdict(h) for h in b.intersection_4of7],
        "intersection_all": [asdict(h) for h in b.intersection_all],
        "total_pass": sum(buc.pass_count for buc in b.buckets),
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


def _sma(xs: list[float], n: int) -> float:
    if not xs or len(xs) < n:
        return 0.0
    window = xs[-n:]
    return sum(window) / n


def _build_hit(
    *,
    sym: str,
    meta: dict[str, Any],
    close: float,
    vol: int,
    vol_ratio: float,
    ma5: float,
    ma20: float,
    ma60: float,
    pct_1d: float,
    passed: list[str],
) -> CondHit:
    reason = "+".join(passed) + f" · {len(passed)}/7"
    return CondHit(
        symbol=sym,
        name=meta.get("name") or sym,
        market=meta.get("market") or "",
        sector=meta.get("sector") or "기타",
        close=round(close, 2),
        vol=int(vol),
        vol_ratio=round(vol_ratio, 2),
        ma5=round(ma5, 2),
        ma20=round(ma20, 2),
        ma60=round(ma60, 2),
        pct_1d=round(pct_1d, 2),
        reason=reason,
    )
