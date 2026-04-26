"""PEAD (Post-Earnings Announcement Drift) — 펀더 + 매크로 결합.

DART 사업보고서/분기보고서 데이터에서 EPS/매출 YoY → 매크로 강세 섹터에서
실적 발표 후 N영업일 drift 측정.

dart 폴더의 CSV에서 EPS 추출 → 매크로 sector strength 결합 → 종목별
"펀더 강세 + 매크로 강세" 후보 선별.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DART_DIR = Path.home() / ".gazua" / "data" / "sectors" / "dart"


@dataclass
class PEADCandidate:
    rank: int
    ticker: str
    corp_name: str
    sector_num: str
    sector_macro_z: float
    eps_yoy_pct: float | None
    revenue_yoy_pct: float | None
    fund_score: float       # 0..1 펀더 강도
    macro_score: float      # 0..1 매크로 강도
    composite: float        # 펀더 × 매크로
    last_year: int
    note: str = ""


def _read_dart_csv(path: Path) -> dict[str, Any]:
    """DART 사업보고서 CSV → 핵심 metric 추출. (간이)
    DART 보고서는 IS/BS/CF 항목 다수 행. 'eps_basic' 또는 '주당순이익' 행에서
    thstrm_amount (당기) / frmtrm_amount (전기) 비교.
    """
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            eps_cur = eps_prev = rev_cur = rev_prev = None
            for row in reader:
                acc = (row.get("account_nm") or "").strip()
                cur = row.get("thstrm_amount")
                prev = row.get("frmtrm_amount")
                if not cur:
                    continue
                if "주당순이익" in acc and eps_cur is None:
                    try:
                        eps_cur = float(str(cur).replace(",", ""))
                        eps_prev = float(str(prev or "0").replace(",", ""))
                    except (TypeError, ValueError):
                        pass
                elif (("매출액" in acc or "수익(매출액)" in acc) and rev_cur is None):
                    try:
                        rev_cur = float(str(cur).replace(",", ""))
                        rev_prev = float(str(prev or "0").replace(",", ""))
                    except (TypeError, ValueError):
                        pass
        return {"eps_cur": eps_cur, "eps_prev": eps_prev,
                "rev_cur": rev_cur, "rev_prev": rev_prev}
    except Exception as e:
        logger.debug("read_dart %s failed: %s", path.name, e)
        return {}


def _yoy_pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev) * 100.0


def compute_pead_candidates(
    *,
    min_macro_z: float = 1.0,
    min_eps_yoy: float = 20.0,
    top_n: int = 30,
) -> list[PEADCandidate]:
    """매크로 강세 섹터 × EPS YoY ≥ min% 종목 후보."""
    if not DART_DIR.exists():
        return []

    # macro strength
    from ky_core.scoring.macro_sector_strength import compute_macro_strength
    from ky_core.scoring.leader_stock import UNIVERSE_TO_WICS, WICS_TO_UNIVERSE

    macro_by_wics = {s.sector_num: s.score for s in compute_macro_strength(method="detrended")}

    # universe → ticker by corp name (assume name match)
    import sqlite3
    DB = Path.home() / ".gazua" / "data" / "ky.db"
    name_to_ticker: dict[str, tuple[str, str]] = {}
    if DB.exists():
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        for r in con.execute(
            "SELECT ticker, name, market FROM universe WHERE is_etf = 0"
        ).fetchall():
            name = (r["name"] or "").strip()
            if name:
                name_to_ticker[name] = (r["ticker"], r["market"])
        con.close()

    # Walk DART files: prefer most recent year per corp
    by_corp: dict[str, list[tuple[int, str, Path]]] = {}
    for f in DART_DIR.glob("*.csv"):
        m = re.match(r"^([a-z]+)__(.+?)_사업보고서_(\d{4})\.csv$", f.name)
        if not m:
            continue
        sector_slug = m.group(1)
        corp_name = m.group(2)
        year = int(m.group(3))
        by_corp.setdefault(corp_name, []).append((year, sector_slug, f))

    candidates: list[PEADCandidate] = []
    for corp_name, items in by_corp.items():
        items.sort(reverse=True)  # latest year first
        if not items:
            continue
        year, sector_slug, latest_path = items[0]
        data = _read_dart_csv(latest_path)
        eps_yoy = _yoy_pct(data.get("eps_cur"), data.get("eps_prev"))
        rev_yoy = _yoy_pct(data.get("rev_cur"), data.get("rev_prev"))

        # macro context — find WICS sector by sector_slug
        wics_num = None
        for w_num, slugs in {
            "01": ["energy", "oil"], "02": ["chemical"], "03": ["nonferrous"],
            "04": ["steel"], "05": ["construction"], "06": ["machinery"],
            "07": ["ship"], "10": ["auto"], "11": ["cosmetic"],
            "12": ["tourism"], "13": ["media"], "14": ["retail"],
            "15": ["food", "staple"], "16": ["food"], "17": ["pharma"],
            "18": ["bank"], "19": ["broker"], "20": ["insurance"],
            "21": ["fincap"], "22": ["software"], "23": ["itservice"],
            "24": ["display"], "25": ["handset"], "26": ["semiconductor"],
            "29": ["telecom"], "30": ["utility"], "31": ["realestate"],
            "32": ["defense"], "33": ["paper"], "34": ["electronics"],
        }.items():
            if sector_slug in slugs:
                wics_num = w_num
                break
        if not wics_num:
            continue
        macro_z = macro_by_wics.get(wics_num, 0.0)
        if macro_z is None or macro_z < min_macro_z:
            continue
        if eps_yoy is None or eps_yoy < min_eps_yoy:
            continue

        ticker_pair = name_to_ticker.get(corp_name)
        ticker = ticker_pair[0] if ticker_pair else "—"

        fund_score = min(eps_yoy / 100.0, 1.0)  # 100% YoY = 1.0
        macro_norm = max(0.0, min(1.0, (macro_z + 3) / 6))
        composite = fund_score * 0.5 + macro_norm * 0.5

        candidates.append(PEADCandidate(
            rank=0,
            ticker=ticker,
            corp_name=corp_name,
            sector_num=wics_num,
            sector_macro_z=round(macro_z, 2),
            eps_yoy_pct=round(eps_yoy, 1) if eps_yoy is not None else None,
            revenue_yoy_pct=round(rev_yoy, 1) if rev_yoy is not None else None,
            fund_score=round(fund_score, 3),
            macro_score=round(macro_norm, 3),
            composite=round(composite, 3),
            last_year=year,
            note=f"EPS+{eps_yoy:.0f}% macro z={macro_z:+.1f}",
        ))

    candidates.sort(key=lambda x: x.composite, reverse=True)
    for i, c in enumerate(candidates[:top_n], 1):
        c.rank = i
    return candidates[:top_n]
