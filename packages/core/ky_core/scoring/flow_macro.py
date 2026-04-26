"""외인 수급 vs 매크로 cells 동조성 분석.

ky.db ohlcv + investor_trend 데이터 (있을 때) 또는 KIS market adapter live data
사용. 매크로 cells 시계열과 외인 누적 매수의 cross-correlation 측정.

이 모듈은 minimal viable: ohlcv/investor 데이터가 풍부할 때 정밀화 가능.
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"


@dataclass
class FlowMacroPair:
    cell_id: str
    sector_num: str
    sector_name: str
    indicator: str
    foreign_lag_months: int   # foreign net buying이 몇 개월 후행/선행
    correlation: float
    n_overlap: int


def get_foreign_net_by_sector(
    days_back: int = 720,
) -> dict[str, list[tuple[str, float]]]:
    """ky.db에 investor_trend 데이터가 있다면 sector별 외인 net buying 시계열.

    schema가 없거나 데이터 부족하면 empty dict 반환.
    """
    if not DB_PATH.exists():
        return {}
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # check available tables
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "investor_trend" not in tables:
            return {}
        # very lenient — actual schema 미확정
        rows = con.execute(
            "SELECT * FROM investor_trend ORDER BY date DESC LIMIT ?",
            (days_back * 50,)  # rough: 50 sectors max
        ).fetchall()
        out: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            d = dict(r)
            sec = str(d.get("sector") or d.get("symbol") or "")
            date = str(d.get("date", ""))
            net = d.get("foreign_net") or d.get("for_net") or 0.0
            try:
                net_v = float(net)
            except (TypeError, ValueError):
                continue
            out.setdefault(sec, []).append((date, net_v))
        return out
    except Exception as e:
        logger.warning("foreign_net query failed: %s", e)
        return {}
    finally:
        con.close()


def compute_flow_macro_correlation(
    macro_method: str = "yoy",
    max_lag_months: int = 6,
) -> list[FlowMacroPair]:
    """매크로 cells × 외인 net buying cross-correlation.

    데이터 가용 시 페어별 lag/corr 계산. 없으면 empty list.
    """
    foreign = get_foreign_net_by_sector()
    if not foreign:
        logger.info("foreign net data not available — returning empty")
        return []

    # Aggregate foreign net by month per sector
    foreign_monthly: dict[str, dict[str, float]] = {}
    for sec, series in foreign.items():
        m_dict: dict[str, list[float]] = {}
        for d, v in series:
            month = d[:7] if len(d) >= 7 else d
            m_dict.setdefault(month, []).append(v)
        foreign_monthly[sec] = {
            m: sum(vs) for m, vs in m_dict.items()  # cumulative net per month
        }

    # Macro signal (yoy z-score) per sector
    from ky_core.scoring.sector_leadlag import _build_sector_series
    macro_series = _build_sector_series(method=macro_method)

    # Pair them by sector — but foreign uses universe.sector, macro uses WICS num
    from ky_core.scoring.leader_stock import UNIVERSE_TO_WICS

    out: list[FlowMacroPair] = []
    for univ_sec, foreign_m in foreign_monthly.items():
        wics = UNIVERSE_TO_WICS.get(univ_sec)
        if not wics or wics not in macro_series:
            continue
        macro = macro_series[wics]
        if len(macro) < 12 or len(foreign_m) < 12:
            continue

        # Align — naive: take last min(len) months
        sorted_months = sorted(foreign_m.keys())
        macro_tail = macro[-len(sorted_months):]
        foreign_tail = [foreign_m[m] for m in sorted_months[-len(macro_tail):]]
        if len(macro_tail) < 12 or len(foreign_tail) < 12:
            continue

        # CCF
        best_lag, best_corr = 0, 0.0
        n = min(len(macro_tail), len(foreign_tail))
        a = macro_tail[-n:]
        b = foreign_tail[-n:]
        for lag in range(-max_lag_months, max_lag_months + 1):
            if lag >= 0:
                x = a[:n - lag]
                y = b[lag:]
            else:
                x = a[-lag:]
                y = b[:n + lag]
            if len(x) < 12 or len(x) != len(y):
                continue
            try:
                c = statistics.correlation(x, y)
            except Exception:
                continue
            if abs(c) > abs(best_corr):
                best_corr = c
                best_lag = lag

        if abs(best_corr) < 0.2:
            continue
        out.append(FlowMacroPair(
            cell_id=f"flow_{wics}",
            sector_num=wics,
            sector_name=univ_sec,
            indicator=f"외인 net buying ({univ_sec})",
            foreign_lag_months=best_lag,
            correlation=round(best_corr, 3),
            n_overlap=n,
        ))

    out.sort(key=lambda x: abs(x.correlation), reverse=True)
    return out
