"""Short-interest proxy scanner — 공매도 / 대차잔고 대시보드.

We do **not** have a live KRX 공매도 포털 feed wired yet (the ETL job will
land separately).  Rather than render a mock page, this scanner surfaces a
price-and-volume derived proxy that is computed off the real ``Panel``:

    - "과열(overheated)" candidate
        heavy recent volume on a negative price trend — these are the
        stocks institutional desks are most likely to be pressing on the
        short side. Flag: price_20d <= -5% AND vol_ratio_20 >= 1.4

    - "숏스퀴즈(squeeze)" candidate
        the opposite: stocks that slid -10%+ over 20d and are now
        reclaiming ground with volume (closed above prior 5-day high and
        vol >= 1.5x avg). These are the highest-probability squeeze names
        given only OHLCV.

Each row explicitly carries ``source="proxy:panel"`` so the UI (and a
downstream real-KRX integration) can distinguish derived numbers from
official short interest when it lands.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any

from ky_core.scanning.loader import Panel, load_panel


# --------------------------------------------------------------------------- #
# Data shapes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class ShortIntRow:
    rank: int
    symbol: str
    name: str
    sector: str
    market: str
    close: float
    pct_5d: float
    pct_20d: float
    vol_ratio_20: float      # last-5 vol / prior-20 vol
    short_proxy_pct: float   # synthetic intensity score (0..100)
    status: str              # "과열" | "주의" | "정상"
    source: str = "proxy:panel"


@dataclass
class SqueezeRow:
    rank: int
    symbol: str
    name: str
    sector: str
    market: str
    close: float
    pct_5d: float
    pct_20d: float
    vol_ratio_5: float
    trigger: str             # short human tag
    risk: str                # Low / Med / High
    short_proxy_pct: float
    source: str = "proxy:panel"


@dataclass
class SectorShort:
    name: str
    members: int
    mean_proxy_pct: float
    overheated: int          # count of overheated members


@dataclass
class ShortIntBundle:
    as_of: str
    universe_size: int
    notice: str
    overheated: list[ShortIntRow]
    squeeze: list[SqueezeRow]
    sector_overheat: list[SectorShort]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def scan_shortint(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
    top_n: int = 10,
) -> ShortIntBundle:
    panel = panel or load_panel(as_of)

    all_rows: list[dict[str, Any]] = []
    for sym, rows in panel.series.items():
        meta = panel.universe.get(sym)
        if not meta or len(rows) < 40:
            continue
        closes = [r["close"] for r in rows]
        vols = [r.get("volume") or 0 for r in rows]

        pct5 = _pct(closes, 5)
        pct20 = _pct(closes, 20)
        vol_ratio_20 = _vol_ratio(vols, 5, 20)
        vol_ratio_5 = _vol_ratio(vols, 3, 20)

        proxy = _proxy_score(pct5=pct5, pct20=pct20, vol_ratio=vol_ratio_20)

        all_rows.append(
            {
                "symbol": sym,
                "name": meta.get("name") or sym,
                "sector": meta.get("sector") or "기타",
                "market": meta.get("market") or "",
                "close": closes[-1],
                "pct_5d": pct5,
                "pct_20d": pct20,
                "vol_ratio_20": vol_ratio_20,
                "vol_ratio_5": vol_ratio_5,
                "proxy": proxy,
                "closes": closes,
            }
        )

    # OVERHEATED — falling stocks with heavy volume
    overheated_src = [
        r for r in all_rows
        if r["pct_20d"] <= -5.0 and r["vol_ratio_20"] >= 1.4
    ]
    overheated_src.sort(key=lambda r: r["proxy"], reverse=True)
    overheated = [
        ShortIntRow(
            rank=i,
            symbol=r["symbol"],
            name=r["name"],
            sector=r["sector"],
            market=r["market"],
            close=round(r["close"], 2),
            pct_5d=round(r["pct_5d"], 2),
            pct_20d=round(r["pct_20d"], 2),
            vol_ratio_20=round(r["vol_ratio_20"], 2),
            short_proxy_pct=round(r["proxy"], 2),
            status=_status(r["proxy"]),
        )
        for i, r in enumerate(overheated_src[:top_n], start=1)
    ]

    # SQUEEZE — bombed-out stocks reclaiming the 5-day high on volume
    squeeze_src: list[dict[str, Any]] = []
    for r in all_rows:
        if r["pct_20d"] > -8.0:
            continue
        closes = r["closes"]
        if len(closes) < 8:
            continue
        prior_high = max(closes[-6:-1])
        if closes[-1] >= prior_high and r["vol_ratio_5"] >= 1.3 and r["pct_5d"] > 0:
            r2 = dict(r)
            r2["_trigger"] = "5d high reclaim"
            r2["_risk"] = _squeeze_risk(r2["pct_20d"], r2["vol_ratio_5"])
            squeeze_src.append(r2)
    squeeze_src.sort(key=lambda r: (r["proxy"], r["vol_ratio_5"]), reverse=True)
    squeeze = [
        SqueezeRow(
            rank=i,
            symbol=r["symbol"],
            name=r["name"],
            sector=r["sector"],
            market=r["market"],
            close=round(r["close"], 2),
            pct_5d=round(r["pct_5d"], 2),
            pct_20d=round(r["pct_20d"], 2),
            vol_ratio_5=round(r["vol_ratio_5"], 2),
            trigger=r["_trigger"],
            risk=r["_risk"],
            short_proxy_pct=round(r["proxy"], 2),
        )
        for i, r in enumerate(squeeze_src[:top_n], start=1)
    ]

    # Sector overheat aggregation
    bucket: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        b = bucket.setdefault(r["sector"], {"sum": 0.0, "n": 0, "oh": 0})
        b["sum"] += r["proxy"]
        b["n"] += 1
        if r in overheated_src:
            b["oh"] += 1
    sector_rows = [
        SectorShort(
            name=sec,
            members=v["n"],
            mean_proxy_pct=round(v["sum"] / v["n"], 2) if v["n"] else 0.0,
            overheated=v["oh"],
        )
        for sec, v in bucket.items()
        if v["n"] >= 3
    ]
    sector_rows.sort(key=lambda s: s.mean_proxy_pct, reverse=True)

    return ShortIntBundle(
        as_of=panel.as_of,
        universe_size=len(panel.universe),
        notice=(
            "공매도/대차 원천 데이터가 아직 연결되지 않아 가격·거래량 기반 "
            "프록시 지표로 산출했습니다. KRX 공매도 포털 ETL 연동 후 교체 예정."
        ),
        overheated=overheated,
        squeeze=squeeze,
        sector_overheat=sector_rows[:15],
    )


def to_dict(b: ShortIntBundle) -> dict[str, Any]:
    return {
        "as_of": b.as_of,
        "universe_size": b.universe_size,
        "notice": b.notice,
        "overheated": [asdict(r) for r in b.overheated],
        "squeeze": [asdict(r) for r in b.squeeze],
        "sector_overheat": [asdict(s) for s in b.sector_overheat],
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


def _pct(closes: list[float], back: int) -> float:
    if len(closes) <= back or closes[-back - 1] <= 0:
        return 0.0
    return (closes[-1] / closes[-back - 1] - 1.0) * 100.0


def _vol_ratio(vols: list[int], short: int, long: int) -> float:
    if len(vols) < long + 1:
        return 0.0
    short_avg = sum(vols[-short:]) / max(1, short)
    long_avg = sum(vols[-long - short: -short]) / max(1, long)
    if long_avg <= 0:
        return 0.0
    return short_avg / long_avg


def _proxy_score(*, pct5: float, pct20: float, vol_ratio: float) -> float:
    """Blended 0..100 score: heavier when price drops while volume rises."""
    # Map price drop (−15% → 60 pts, 0% → 0), vol surge (1x → 0, 2x → 30 pts).
    drop_score = max(0.0, min(60.0, -pct20 * 4))
    vol_score = max(0.0, min(30.0, (vol_ratio - 1.0) * 30))
    momentum_pen = max(0.0, min(10.0, -pct5 * 2))  # accelerating drop penalty
    return drop_score + vol_score + momentum_pen


def _status(proxy: float) -> str:
    if proxy >= 60:
        return "과열"
    if proxy >= 35:
        return "주의"
    return "정상"


def _squeeze_risk(pct20: float, vol5: float) -> str:
    # Risk of squeeze failing = combination of moderate drop + low volume.
    if pct20 <= -20 and vol5 >= 2.0:
        return "High"
    if pct20 <= -12 and vol5 >= 1.5:
        return "Med"
    return "Low"
