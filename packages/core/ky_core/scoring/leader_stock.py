"""Leader stock — 종목 단위 점수 (매크로 sector 강도 + 가격 RS + 펀더 신호).

기존 ``ky_core.scanning.leader_scan`` 은 가격/SEPA 기반 (TT + RS + VCP + EPS).
이 모듈은 그 점수를 받아 ``macro_sector_strength`` 의 sector score를 추가
가중치로 결합한 "macro-aware leader score"를 산출한다.

Composite formula:
    macro_leader = 0.40 * sector_macro + 0.40 * price_rs + 0.20 * fund_signal

Args knobs:
- macro_method: "detrended" (모멘텀) / "raw" (level)
- min_filled: sector_macro에 반영하는 cell 최소 수 (default 5)
- price_rs_source: "scanning.leader_scan" (default) / 또는 None (skip)

ky.db universe 테이블 (ticker × sector) 활용. WICS 34섹터 매핑은
``apps/api/routers/research/sector_indicators.py`` 의 WICS_SECTORS 와 동일하나
이 모듈에서는 자체 로컬 매핑을 보유 (코어 패키지가 apps에 의존 안 하도록).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ky_core.scoring.macro_sector_strength import (
    SectorMacroStrength,
    compute_macro_strength,
)

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"


# WICS 섹터 → universe.sector 한국어 매핑 (apps/api 와 동기 유지)
WICS_TO_UNIVERSE: dict[str, list[str]] = {
    "01": ["에너지", "석유/가스"],
    "02": ["화학"],
    "03": ["금속", "비금속"],
    "04": ["금속", "철강"],
    "05": ["건설", "건설 및 엔지니어링"],
    "06": ["일반기계", "기계 및 장비"],
    "07": ["조선/기계"],
    "08": ["상사/자본재", "기타금융"],
    "09": ["운송/창고"],
    "10": ["자동차", "자동차 및 부품"],
    "11": ["섬유/의류", "섬유 및 의류"],
    "12": ["서비스/레저", "호텔/레저"],
    "13": ["미디어/엔터", "교육서비스"],
    "14": ["유통/소매", "도매업"],
    "15": ["식료품/생필품"],
    "16": ["음식료품", "담배"],
    "17": ["제약 및 바이오", "의약/생명과학"],
    "18": ["은행"],
    "19": ["증권"],
    "20": ["보험"],
    "21": ["기타금융", "금융"],
    "22": ["IT 서비스 및 컨설팅", "소프트웨어"],
    "23": ["IT 서비스 및 컨설팅"],
    "24": ["디스플레이"],
    "25": ["전기/전자", "통신장비"],
    "26": ["반도체", "반도체 및 관련장비"],
    "27": ["전기/전자", "가전제품"],
    "28": ["전기/전자"],
    "29": ["통신서비스"],
    "30": ["전기/가스/수도"],
    "31": ["부동산", "부동산 (REITs)"],
    "32": ["방산", "운수장비"],
    "33": ["종이/목재"],
    "34": ["전기/전자"],
}

# Reverse: universe.sector → WICS sector_num (first match wins)
UNIVERSE_TO_WICS: dict[str, str] = {}
for wics_num, univ_list in WICS_TO_UNIVERSE.items():
    for u in univ_list:
        UNIVERSE_TO_WICS.setdefault(u, wics_num)


# --------------------------------------------------------------------------- #
# Score normalization                                                         #
# --------------------------------------------------------------------------- #


def _to_unit(value: float | None, lo: float = -3.0, hi: float = 3.0) -> float:
    """z-score 등을 [0, 1]로 정규화. None은 0.5 (중립)."""
    if value is None:
        return 0.5
    if not isinstance(value, (int, float)):
        return 0.5
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    return (value - lo) / (hi - lo)


# --------------------------------------------------------------------------- #
# Output dataclass                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class LeaderStock:
    rank: int
    ticker: str
    name: str
    market: str
    universe_sector: str
    wics_sector_num: str
    wics_sector_name: str
    leader_score: float       # 0..1 composite
    macro_score: float        # 0..1 (sector macro strength normalized)
    macro_z: float | None     # raw sector z-score
    price_rs: float           # 0..1 placeholder (future: integrate ohlcv RS)
    fund_signal: float        # 0..1 placeholder (future: DART YoY EPS / ROE)
    macro_top_signals: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def _load_price_leaders() -> dict[str, dict[str, float]]:
    """기존 ``ky_core.scanning.leader_scan.scan_leaders`` 결과를 가져와
    ticker → {price_rs, tt, vcp, eps} 매핑 dict 반환. ohlcv 데이터가 없으면 {}.
    """
    try:
        from ky_core.scanning.leader_scan import scan_leaders
    except Exception as exc:
        logger.warning("scan_leaders unavailable: %s", exc)
        return {}

    try:
        leaders = scan_leaders(top_n=2000, min_leader_score=0.0)
    except Exception as exc:
        logger.warning("scan_leaders failed: %s", exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    for L in leaders:
        rs_pct = getattr(L, "rs_percentile", None)
        out[L.symbol] = {
            "price_rs": (rs_pct / 100.0) if rs_pct else 0.5,
            "tt": (getattr(L, "tt_passes", 0) or 0) / 8.0,
            "vcp": 1.0 if (getattr(L, "vcp_stage", 0) or 0) >= 3 else 0.5,
            "eps": getattr(L, "eps_signal", 0.5),
        }
    return out


def scan_leader_stocks(
    *,
    macro_method: str = "detrended",
    min_filled: int = 5,
    universe_filter: Optional[Iterable[str]] = None,
    top_n: int = 50,
    use_price_rs: bool = True,
) -> list[LeaderStock]:
    """Macro-aware leader stock ranking.

    Composite (when use_price_rs=True):
        leader = 0.30·price_rs + 0.20·tt + 0.15·vcp + 0.10·eps + 0.25·macro_sector

    Composite (placeholder mode, no ohlcv):
        leader = 0.40·macro + 0.40·0.5 + 0.20·0.5

    Args:
        macro_method: 'detrended' (default) / 'raw'
        min_filled: sector_macro 최소 filled cell 수
        universe_filter: ticker 리스트만 (None=전체)
        top_n: 결과 개수
        use_price_rs: True면 scanning.leader_scan 결합, False면 placeholder
    """
    strengths: list[SectorMacroStrength] = compute_macro_strength(method=macro_method)
    by_wics: dict[str, SectorMacroStrength] = {s.sector_num: s for s in strengths}

    price_map: dict[str, dict[str, float]] = {}
    if use_price_rs:
        price_map = _load_price_leaders()
        logger.info("price_map loaded: %d tickers", len(price_map))

    if not DB_PATH.exists():
        logger.warning("ky.db not found at %s", DB_PATH)
        return []

    rows: list[LeaderStock] = []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT ticker, name, market, sector
        FROM universe
        WHERE is_etf = 0 AND sector IS NOT NULL AND sector <> ''
    """
    if universe_filter:
        placeholders = ",".join(["?"] * len(list(universe_filter)))
        sql += f" AND ticker IN ({placeholders})"
        params: list[Any] = list(universe_filter)
    else:
        params = []

    for r in con.execute(sql, params).fetchall():
        univ_sector = (r["sector"] or "").strip()
        wics_num = UNIVERSE_TO_WICS.get(univ_sector)
        if not wics_num:
            continue
        s = by_wics.get(wics_num)
        if not s or s.n_filled < min_filled:
            continue

        macro_z = s.score
        macro_norm = _to_unit(macro_z)

        ticker = r["ticker"]
        pdata = price_map.get(ticker, {})
        price_rs = pdata.get("price_rs", 0.5)
        tt = pdata.get("tt", 0.0)
        vcp = pdata.get("vcp", 0.5)
        eps = pdata.get("eps", 0.5)
        has_price = ticker in price_map

        if has_price and use_price_rs:
            leader_score = (0.30 * price_rs + 0.20 * tt + 0.15 * vcp
                           + 0.10 * eps + 0.25 * macro_norm)
        else:
            leader_score = 0.40 * macro_norm + 0.40 * price_rs + 0.20 * eps

        top_sig_str = ""
        if s.top_signals:
            t0 = s.top_signals[0]
            top_sig_str = f"{t0['indicator']} z={t0['z']:+.1f}"
        reason_parts = [f"macro #{s.rank}"]
        if top_sig_str:
            reason_parts.append(top_sig_str)
        if has_price:
            reason_parts.append(f"RS={price_rs*100:.0f}")
            if tt >= 0.625:  # 5/8 이상
                reason_parts.append(f"TT={int(tt*8)}/8")
            if vcp >= 1.0:
                reason_parts.append("VCP")

        rows.append(LeaderStock(
            rank=0,
            ticker=ticker,
            name=r["name"] or ticker,
            market=r["market"] or "",
            universe_sector=univ_sector,
            wics_sector_num=wics_num,
            wics_sector_name=s.sector_name,
            leader_score=round(leader_score, 4),
            macro_score=round(macro_norm, 3),
            macro_z=round(macro_z, 3) if macro_z is not None else None,
            price_rs=round(price_rs, 3),
            fund_signal=round(eps, 3),
            macro_top_signals=s.top_signals,
            reason=" · ".join(reason_parts),
        ))
    con.close()

    rows.sort(key=lambda x: x.leader_score, reverse=True)
    for i, r in enumerate(rows, 1):
        r.rank = i
    return rows[:top_n]
