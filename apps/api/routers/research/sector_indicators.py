"""Sector-indicators router — WICS 34섹터 × 10지표 매트릭스 시각화 API.

Endpoints:
  GET /api/v1/research/sector-indicators/matrix
      → 340 cells의 (sector, indicator, source, latest_value, rows, sparkline_thumb)
      → 캐시: in-memory 5분
  GET /api/v1/research/sector-indicators/series/{cell_id}
      → 단일 cell의 30년 시계열 (period: 1y/3y/10y/all)
  GET /api/v1/research/sector-indicators/symbols?sector={code}
      → ky.db universe의 sector × ticker 매핑
  GET /api/v1/research/sector-indicators/sectors
      → WICS 34 sector 메타정보 (이름·태그·universe sector 매핑)
"""
from __future__ import annotations

import csv
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from routers.research._shared import envelope

logger = logging.getLogger("routers.research.sector_indicators")
router = APIRouter(prefix="/sector-indicators", tags=["research-sector-indicators"])


# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #

DATA_ROOT = Path.home() / ".gazua" / "data" / "sectors"
DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"
MAP_PATH = (
    Path(__file__).resolve().parents[4] / "docs" / "SECTOR_INDICATOR_MAP.md"
)


# --------------------------------------------------------------------------- #
# Sector metadata — WICS 34 ↔ universe.sector 매핑                           #
# --------------------------------------------------------------------------- #

# (number, slug, label, universe_sector, primary_etf)
WICS_SECTORS: list[tuple[str, str, str, list[str], Optional[str]]] = [
    ("01", "energy",        "에너지",            ["에너지", "석유/가스"],            "117460.KS"),
    ("02", "chemical",      "화학",              ["화학"],                          "091230.KS"),
    ("03", "nonferrous",    "비철금속",          ["금속", "비금속"],                 "139250.KS"),
    ("04", "steel",         "철강",              ["금속", "철강"],                   "117680.KS"),
    ("05", "construction",  "건설",              ["건설", "건설 및 엔지니어링"],     "117700.KS"),
    ("06", "machinery",     "기계",              ["일반기계", "기계 및 장비"],       None),
    ("07", "ship",          "조선",              ["조선/기계"],                      "102960.KS"),
    ("08", "trade",         "상사·자본재",       ["상사/자본재", "기타금융"],        None),
    ("09", "transport",     "운송",              ["운송/창고"],                      "SEA"),
    ("10", "auto",          "자동차·부품",       ["자동차", "자동차 및 부품"],       "091180.KS"),
    ("11", "cosmetic",      "화장품·의류",       ["섬유/의류", "섬유 및 의류"],      "228790.KS"),
    ("12", "tourism",       "호텔·레저",         ["서비스/레저", "호텔/레저"],       None),
    ("13", "media",         "미디어·교육",       ["미디어/엔터", "교육서비스"],      "300610.KS"),
    ("14", "retail",        "유통",              ["유통/소매", "도매업"],            "091170.KS"),
    ("15", "staple",        "필수소비재",        ["식료품/생필품"],                  None),
    ("16", "food",          "식품·담배",         ["음식료품", "담배"],               "140710.KS"),
    ("17", "pharma",        "제약·바이오",       ["제약 및 바이오", "의약/생명과학"],"266420.KS"),
    ("18", "bank",          "은행",              ["은행"],                           None),
    ("19", "broker",        "증권",              ["증권"],                           "102970.KS"),
    ("20", "insurance",     "보험",              ["보험"],                           "140700.KS"),
    ("21", "fincap",        "다각화 금융",       ["기타금융", "금융"],               None),
    ("22", "software",      "소프트웨어",        ["IT 서비스 및 컨설팅", "소프트웨어"], "139260.KS"),
    ("23", "itservice",     "IT 서비스",         ["IT 서비스 및 컨설팅"],            "266370.KS"),
    ("24", "display",       "디스플레이",        ["디스플레이"],                     "139220.KS"),
    ("25", "handset",       "핸드셋",            ["전기/전자", "통신장비"],          None),
    ("26", "semiconductor", "반도체",            ["반도체", "반도체 및 관련장비"],   "091160.KS"),
    ("27", "itappliance",   "IT 가전",           ["전기/전자", "가전제품"],          None),
    ("28", "electric",      "전기장비",          ["전기/전자"],                      None),
    ("29", "telecom",       "통신 서비스",       ["통신서비스"],                     "139310.KS"),
    ("30", "utility",       "유틸리티",          ["전기/가스/수도"],                 None),
    ("31", "realestate",    "부동산",            ["부동산", "부동산 (REITs)"],       "157500.KS"),
    ("32", "defense",       "우주항공·방위",     ["방산", "운수장비"],               "449290.KS"),
    ("33", "paper",         "종이·목재",         ["종이/목재"],                      None),
    ("34", "electronics",   "일반 전기전자",     ["전기/전자"],                      None),
]


# --------------------------------------------------------------------------- #
# Map parser (parse SECTOR_INDICATOR_MAP.md)                                  #
# --------------------------------------------------------------------------- #

_SECTOR_RE = re.compile(r"^##\s*\[(\d{2})\]\s+(.+?)\s*$", re.M)
_ROW_RE = re.compile(
    r"^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
    re.M,
)


def _parse_map() -> list[dict[str, Any]]:
    text = MAP_PATH.read_text(encoding="utf-8")
    matches = list(_SECTOR_RE.finditer(text))
    cells: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        sec_num = m.group(1)
        sec_name = m.group(2).strip()
        chunk = text[m.end():(matches[i+1].start() if i+1 < len(matches) else len(text))]
        for row in _ROW_RE.findall(chunk):
            idx, indicator, source_raw, category, leadlag = row
            cells.append({
                "cell_id": f"{sec_num}_{idx.zfill(2)}",
                "sector_num": sec_num,
                "sector_name": sec_name,
                "idx": int(idx),
                "indicator": indicator.strip(),
                "source_raw": source_raw.strip(),
                "category": category.strip(),
                "leadlag": leadlag.strip(),
            })
    return cells


# --------------------------------------------------------------------------- #
# Disk index (CSV inventory)                                                  #
# --------------------------------------------------------------------------- #

_DISK_INDEX: list[tuple[str, str, int]] | None = None
_DISK_INDEX_TS: float = 0.0
_DISK_TTL = 300.0  # 5 min


def _disk_index() -> list[tuple[str, str, int]]:
    """Return list of (folder, slug, rows). Cached 5 min."""
    global _DISK_INDEX, _DISK_INDEX_TS
    now = time.time()
    if _DISK_INDEX and (now - _DISK_INDEX_TS) < _DISK_TTL:
        return _DISK_INDEX

    out: list[tuple[str, str, int]] = []

    def _walk(d: Path, prefix: str = "") -> None:
        try:
            for c in d.iterdir():
                if c.is_dir():
                    _walk(c, prefix + c.name + "/")
                elif c.suffix == ".csv":
                    try:
                        n = sum(1 for _ in c.open(encoding="utf-8", errors="ignore")) - 1
                    except Exception:
                        n = 0
                    out.append((prefix.rstrip("/"), c.stem, max(n, 0)))
        except Exception:
            pass

    if DATA_ROOT.exists():
        _walk(DATA_ROOT)

    _DISK_INDEX = out
    _DISK_INDEX_TS = now
    return out


# --------------------------------------------------------------------------- #
# Cell → CSV resolver                                                         #
# --------------------------------------------------------------------------- #

SECTOR_TAGS = {s[0]: [s[1]] + ([t.lower() for t in s[2:3]] if False else []) for s in WICS_SECTORS}
# Build proper sector → list of tag prefixes
_SECTOR_PREFIXES: dict[str, list[str]] = {}
for sec_num, slug, _label, _univ, _etf in WICS_SECTORS:
    prefs = [slug]
    if slug == "energy":
        prefs.append("oil")
    elif slug == "chemical":
        prefs.append("oil")
    elif slug == "trade":
        prefs.append("retail")
    elif slug == "fincap":
        prefs.append("bank")
    elif slug == "electronics":
        prefs.extend(["itappliance", "electric"])
    _SECTOR_PREFIXES[sec_num] = prefs


SOURCE_FOLDERS = {
    "ECOS": ["ecos", "imported/ecos"],
    "FRED": ["fred", "imported/fred"],
    "KOSIS": ["kosis", "imported/kosis"],
    "KIS": ["kis_index", "yf_commodities"],
    "DART": ["dart"],
    "EIA": ["eia"],
    "TRASS": ["customs"],
    "OECD": ["oecd"],
    "WB": ["worldbank"],
    "pytrends": ["pytrends"],
    "CFTC": ["cftc"],
    "Comtrade": ["un_comtrade"],
    "Crawl": ["yf_commodities", "openfda", "stooq"],
    "yfinance": ["yf_commodities"],
}


def _parse_sources(src: str) -> list[str]:
    folders: list[str] = []
    for tok, fs in SOURCE_FOLDERS.items():
        if tok in src:
            folders.extend(fs)
    return folders


def _resolve_cell(cell: dict[str, Any]) -> Optional[tuple[str, str, int]]:
    """Pick best disk CSV for a cell. Returns (folder, slug, rows) or None."""
    src_folders = _parse_sources(cell["source_raw"])
    sec_prefs = _SECTOR_PREFIXES.get(cell["sector_num"], [])
    src = cell["source_raw"]
    csvs = _disk_index()

    # 1. sector tag prefix in any source folder
    for tag in sec_prefs:
        for fld, slug, rows in csvs:
            if rows < 100:
                continue
            sl = slug.lower()
            if not (sl.startswith(tag + "__") or sl.startswith(tag + "_")):
                continue
            if not src_folders or fld in src_folders or any(fld.startswith(sf) for sf in src_folders):
                return (fld, slug, rows)

    # 2. KOSIS macro presence
    if "KOSIS" in src:
        for fld, slug, rows in csvs:
            if fld in ("kosis", "imported/kosis") and rows >= 100:
                return (fld, slug, rows)

    # 3. ECOS
    if "ECOS" in src:
        for fld, slug, rows in csvs:
            if fld in ("ecos", "imported/ecos") and rows >= 100:
                return (fld, slug, rows)

    # 4. FRED
    if "FRED" in src:
        for fld, slug, rows in csvs:
            if fld in ("fred", "imported/fred") and rows >= 100:
                return (fld, slug, rows)

    # 5. EIA
    if "EIA" in src:
        for fld, slug, rows in csvs:
            if fld == "eia" and rows >= 100:
                return (fld, slug, rows)

    # 6. DART corp match
    if "DART" in src:
        for fld, slug, rows in csvs:
            if fld != "dart" or rows < 50:
                continue
            parts = slug.split("__", 1)
            if len(parts) < 2:
                continue
            corp = parts[1].split("_사업보고서")[0]
            if corp and (corp in cell["indicator"] or (len(corp) >= 3 and corp[:3] in cell["indicator"])):
                return (fld, slug, rows)

    # 7. KIS index/ETF
    if "KIS" in src:
        for tag in sec_prefs:
            for fld, slug, rows in csvs:
                sl = slug.lower()
                if fld in ("kis_index", "yf_commodities") and (sl.startswith(tag + "__") or sl.startswith(tag + "_")) and rows >= 100:
                    return (fld, slug, rows)
        for fld, slug, rows in csvs:
            if fld == "kis_index" and slug.lower().startswith("market__") and rows >= 100:
                return (fld, slug, rows)

    # 8. TRASS customs
    if "TRASS" in src:
        for tag in sec_prefs:
            for fld, slug, rows in csvs:
                sl = slug.lower()
                if fld == "customs" and (sl.startswith(tag + "__") or sl.startswith(tag + "_")) and rows >= 100:
                    return (fld, slug, rows)

    # 9. OECD/WB/CFTC/pytrends/Comtrade folder presence
    for tok in ["OECD", "WB", "CFTC", "pytrends", "Comtrade"]:
        if tok in src:
            for fld, slug, rows in csvs:
                if any(fld == f or fld.startswith(f) for f in SOURCE_FOLDERS[tok]) and rows >= 20:
                    return (fld, slug, rows)

    # 10. Crawl/yfinance fallback
    if "Crawl" in src or "yfinance" in src:
        for tag in sec_prefs:
            for fld, slug, rows in csvs:
                sl = slug.lower()
                if fld == "yf_commodities" and (sl.startswith(tag + "__") or sl.startswith(tag + "_")) and rows >= 100:
                    return (fld, slug, rows)

    return None


# --------------------------------------------------------------------------- #
# Read CSV (extract value column + dates)                                     #
# --------------------------------------------------------------------------- #

# Column hints per source (folder -> (date_col, value_col))
_VALUE_COLS = {
    "fred":            ("date", "value"),
    "imported/fred":   ("date", "value"),
    "ecos":            ("date", "value"),
    "imported/ecos":   ("date", "value"),
    "kosis":           ("PRD_DE", "DT"),
    "kis_index":       ("stck_bsop_date", "bstp_nmix_prpr"),
    "yf_commodities":  ("date", "close"),
    "eia":             ("date", "value"),
    "oecd":            ("period", "value"),
    "worldbank":       ("date", "value"),
    "cftc":            ("report_date", "managed_money_long"),
    "un_comtrade":     ("period", "trade_value_usd"),
    "pytrends":        ("date", "value"),
    "openfda":         ("month", "approvals"),
    "customs":         ("period", "export_usd"),
    "stooq":           ("date", "close"),
}


def _read_series(folder: str, slug: str) -> list[tuple[str, float]]:
    """Read a CSV and return list of (date_str, value) sorted ascending."""
    path = DATA_ROOT / folder / f"{slug}.csv"
    if not path.exists():
        return []
    date_col, val_col = _VALUE_COLS.get(folder, ("date", "value"))
    out: list[tuple[str, float]] = []
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = (row.get(date_col) or "").strip()
                v = row.get(val_col)
                if not d or v is None or v == "":
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                out.append((d, fv))
    except Exception as e:
        logger.warning("read_series %s/%s failed: %s", folder, slug, e)
        return []
    out.sort(key=lambda x: x[0])
    return out


def _summarize_series(series: list[tuple[str, float]]) -> dict[str, Any]:
    """Compute latest, 12M%, 3Y z-score, sparkline thumb (30 points)."""
    if not series:
        return {
            "latest": None,
            "latest_date": None,
            "rows": 0,
            "yoy_pct": None,
            "z3y": None,
            "sparkline": [],
        }
    n = len(series)
    latest = series[-1][1]
    latest_date = series[-1][0]

    # 12M ago value (approximate by row index)
    yoy_pct = None
    look_back = max(1, min(n - 1, 252)) if "kis_index" in str(series) else max(1, min(n - 1, 12))
    # safer: pick by row-count quanta — daily=252, monthly=12, weekly=52
    if n > 12:
        # heuristic: detect freq by dates
        try:
            from datetime import datetime as _dt
            d_last = _dt.fromisoformat((latest_date[:10]).replace(".", "-")) if "-" in latest_date or "." in latest_date else None
        except Exception:
            d_last = None
        if d_last is None:
            look_back = min(12, n - 1)
        else:
            # find idx 1 year before
            target_year = d_last.year - 1
            target_month = d_last.month
            cutoff = f"{target_year:04d}-{target_month:02d}"
            look_back = 0
            for i in range(n - 1, -1, -1):
                if series[i][0] <= cutoff:
                    look_back = n - 1 - i
                    break
            if look_back == 0:
                look_back = min(12, n - 1)
        prev = series[-1 - look_back][1]
        if prev not in (0, None):
            yoy_pct = (latest - prev) / abs(prev) * 100.0

    # 3Y z-score (last 36 monthly periods or 756 daily periods)
    z3y = None
    sample = [v for _, v in series[-min(n, 756):]]  # liberal: last 756 rows
    if len(sample) >= 30:
        try:
            mean = sum(sample) / len(sample)
            var = sum((x - mean) ** 2 for x in sample) / len(sample)
            std = var ** 0.5
            if std > 0:
                z3y = (latest - mean) / std
        except Exception:
            z3y = None

    # Sparkline: 30 evenly-spaced points
    spark = []
    if n >= 30:
        step = n / 30
        spark = [series[int(i * step)][1] for i in range(30)]
    else:
        spark = [v for _, v in series]

    return {
        "latest": latest,
        "latest_date": latest_date,
        "rows": n,
        "yoy_pct": yoy_pct,
        "z3y": z3y,
        "sparkline": spark,
    }


# --------------------------------------------------------------------------- #
# Cache                                                                       #
# --------------------------------------------------------------------------- #

_MATRIX_CACHE: dict[str, Any] | None = None
_MATRIX_TS: float = 0.0
_MATRIX_TTL = 300.0


# --------------------------------------------------------------------------- #
# Endpoints                                                                   #
# --------------------------------------------------------------------------- #


@router.get("/sectors")
def get_sectors() -> dict[str, Any]:
    """WICS 34섹터 메타정보."""
    rows = []
    for sec_num, slug, label, univ, etf in WICS_SECTORS:
        rows.append({
            "sector_num": sec_num,
            "slug": slug,
            "label": label,
            "universe_sectors": univ,
            "primary_etf": etf,
        })
    return envelope(data={"sectors": rows, "count": len(rows)})


@router.get("/matrix")
def get_matrix() -> dict[str, Any]:
    """340 cells의 전체 매트릭스 — sector × indicator 그리드 + 각 cell의 요약."""
    global _MATRIX_CACHE, _MATRIX_TS
    now = time.time()
    if _MATRIX_CACHE and (now - _MATRIX_TS) < _MATRIX_TTL:
        return envelope(data=_MATRIX_CACHE)

    cells = _parse_map()
    out_cells = []
    filled = 0
    for c in cells:
        match = _resolve_cell(c)
        if match:
            folder, slug, rows = match
            series = _read_series(folder, slug)
            summary = _summarize_series(series)
            filled += 1 if summary["rows"] > 0 else 0
            out_cells.append({
                **c,
                "filled": True,
                "folder": folder,
                "slug": slug,
                "rows": rows,
                "summary": summary,
            })
        else:
            out_cells.append({
                **c,
                "filled": False,
                "folder": None,
                "slug": None,
                "rows": 0,
                "summary": {"latest": None, "latest_date": None, "rows": 0,
                            "yoy_pct": None, "z3y": None, "sparkline": []},
            })

    body = {
        "cells": out_cells,
        "total_cells": len(cells),
        "filled_cells": filled,
        "fill_pct": round(filled * 100.0 / max(1, len(cells)), 2),
        "sector_count": 34,
        "indicator_per_sector": 10,
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
    }
    _MATRIX_CACHE = body
    _MATRIX_TS = now
    return envelope(data=body)


@router.get("/series/{cell_id}")
def get_series(cell_id: str, period: str = Query("all", pattern="^(1y|3y|10y|all)$")) -> dict[str, Any]:
    """단일 cell의 시계열. period: 1y / 3y / 10y / all."""
    cells = _parse_map()
    cell = next((c for c in cells if c["cell_id"] == cell_id), None)
    if not cell:
        raise HTTPException(status_code=404, detail=f"cell_id={cell_id} not found")
    match = _resolve_cell(cell)
    if not match:
        return envelope(data={"cell": cell, "series": [], "period": period})
    folder, slug, _rows = match
    series = _read_series(folder, slug)

    # Period filter
    if period != "all" and series:
        # Approximate by row count
        n = len(series)
        if period == "1y":
            cut = max(1, min(n, 365))
        elif period == "3y":
            cut = max(1, min(n, 365 * 3))
        elif period == "10y":
            cut = max(1, min(n, 365 * 10))
        else:
            cut = n
        # Use date-based cut if possible, fall back to row-based
        try:
            from datetime import datetime, timedelta
            last_date_str = series[-1][0][:10].replace(".", "-")
            last_date = datetime.fromisoformat(last_date_str)
            years = {"1y": 1, "3y": 3, "10y": 10}[period]
            cutoff_str = (last_date - timedelta(days=years * 366)).strftime("%Y-%m-%d")
            series = [(d, v) for d, v in series if d[:10].replace(".", "-") >= cutoff_str]
        except Exception:
            series = series[-cut:]

    out = [{"date": d, "value": v} for d, v in series]
    return envelope(data={"cell": cell, "series": out, "period": period,
                          "folder": folder, "slug": slug, "n": len(out)})


@router.get("/strength")
def get_strength(
    method: str = Query("detrended", pattern="^(detrended|raw)$",
                        description="detrended: YoY% z-score (인플레 bias 제거) / raw: level z-score")
) -> dict[str, Any]:
    """매트릭스 기반 sector aggregate strength score.

    - 선행 weight 1.5, 동행 1.0, 후행 0.5
    - '역' indicator는 z 부호 반전
    - method=detrended (default): YoY% 변화율 기반 — 모멘텀 강도
    - method=raw: level 기반 — 절대 위치
    - rank desc (강한 섹터 순)
    """
    try:
        from ky_core.scoring.macro_sector_strength import compute_macro_strength
        strengths = compute_macro_strength(method=method)
        rows = [s.as_dict() for s in strengths]
        return envelope(data={
            "rows": rows,
            "count": len(rows),
            "method": method,
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("strength compute failed")
        return envelope(error={"code": "STRENGTH_FAIL", "message": str(e)[:200]})


# --------------------------------------------------------------------------- #
# Phase 2 endpoints (8.1 ~ 8.12)                                              #
# --------------------------------------------------------------------------- #


@router.get("/quality/snapshot")
def get_quality_snapshot(persist: bool = Query(False)) -> dict[str, Any]:
    """현재 데이터 품질 스냅샷. persist=true면 ky.db에 저장."""
    try:
        from ky_core.scoring.quality_monitor import (
            take_snapshot, store_snapshot, detect_regression,
        )
        from dataclasses import asdict
        snap = take_snapshot()
        regression = detect_regression(snap)
        rid = store_snapshot(snap) if persist else None
        return envelope(data={
            **asdict(snap),
            "row_id": rid,
            "regression": regression,
        })
    except Exception as e:
        logger.exception("quality snapshot failed")
        return envelope(error={"code": "QUALITY_FAIL", "message": str(e)[:200]})


@router.get("/quality/history")
def get_quality_history(limit: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """최근 quality snapshot 이력."""
    try:
        from ky_core.scoring.quality_monitor import history
        return envelope(data={"history": history(limit=limit)})
    except Exception as e:
        return envelope(error={"code": "QUALITY_HISTORY_FAIL", "message": str(e)[:200]})


@router.get("/stocks/matrix")
def get_stock_matrix(top_n: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """종목 매트릭스 — 4,516 KRX × 10 metric → composite ranking."""
    try:
        from ky_core.scoring.stock_matrix import compute_stock_matrix
        rows = compute_stock_matrix(top_n=top_n)
        return envelope(data={
            "rows": [r.to_dict() for r in rows],
            "count": len(rows),
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("stock_matrix failed")
        return envelope(error={"code": "STOCK_MATRIX_FAIL", "message": str(e)[:200]})


@router.get("/ask")
def get_ask(q: str = Query(..., min_length=2),
            use_llm: bool = Query(True)) -> dict[str, Any]:
    """LLM 자연어 질문 — 매트릭스 컨텍스트로 답변."""
    try:
        from ky_core.scoring.llm_ask import ask_matrix
        from dataclasses import asdict
        resp = ask_matrix(q, use_llm=use_llm)
        return envelope(data=asdict(resp))
    except Exception as e:
        logger.exception("ask failed")
        return envelope(error={"code": "ASK_FAIL", "message": str(e)[:200]})


@router.get("/flow_macro")
def get_flow_macro() -> dict[str, Any]:
    """외인 수급 × 매크로 cross-correlation."""
    try:
        from ky_core.scoring.flow_macro import compute_flow_macro_correlation
        from dataclasses import asdict
        pairs = compute_flow_macro_correlation()
        return envelope(data={
            "pairs": [asdict(p) for p in pairs],
            "count": len(pairs),
        })
    except Exception as e:
        return envelope(error={"code": "FLOW_MACRO_FAIL", "message": str(e)[:200]})


@router.get("/calendar")
def get_calendar(months_ahead: int = Query(3, ge=1, le=12)) -> dict[str, Any]:
    """이벤트 캘린더 — 한은/Fed/실적시즌."""
    try:
        from ky_core.scoring.events import get_calendar as _cal
        from dataclasses import asdict
        events = _cal(months_ahead=months_ahead)
        return envelope(data={"events": [asdict(e) for e in events],
                              "count": len(events)})
    except Exception as e:
        return envelope(error={"code": "CALENDAR_FAIL", "message": str(e)[:200]})


@router.get("/pead")
def get_pead(min_macro_z: float = Query(1.0, ge=-3.0, le=3.0),
             min_eps_yoy: float = Query(20.0, ge=-100, le=500),
             top_n: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    """PEAD 후보 — 매크로 강세 섹터 × EPS YoY ≥ min%."""
    try:
        from ky_core.scoring.pead import compute_pead_candidates
        rows = compute_pead_candidates(min_macro_z=min_macro_z,
                                        min_eps_yoy=min_eps_yoy, top_n=top_n)
        from dataclasses import asdict
        return envelope(data={"rows": [asdict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return envelope(error={"code": "PEAD_FAIL", "message": str(e)[:200]})


@router.get("/paper/positions")
def get_paper_positions() -> dict[str, Any]:
    """현재 paper trading 포지션 + P&L."""
    try:
        from ky_core.scoring.paper_trade import list_positions
        from dataclasses import asdict
        positions = list_positions()
        return envelope(data={
            "positions": [asdict(p) for p in positions],
            "count": len(positions),
            "total_pnl_krw": sum(p.pnl_krw for p in positions),
        })
    except Exception as e:
        return envelope(error={"code": "PAPER_POS_FAIL", "message": str(e)[:200]})


@router.get("/paper/signal")
def get_paper_signal(top_n: int = Query(3, ge=1, le=10),
                     dry_run: bool = Query(True)) -> dict[str, Any]:
    """EOD 신호 → paper 매매 액션 생성 (dry-run default)."""
    try:
        from ky_core.scoring.paper_trade import daily_signal_to_orders
        return envelope(data=daily_signal_to_orders(top_n=top_n, dry_run=dry_run))
    except Exception as e:
        return envelope(error={"code": "PAPER_SIGNAL_FAIL", "message": str(e)[:200]})


@router.post("/strategy/run")
def post_strategy_run(strategy: dict[str, Any]) -> dict[str, Any]:
    """사용자 정의 strategy DSL 실행 → ranking. 또는 preset id 호출."""
    try:
        from ky_core.scoring.alpha_library import (
            run_strategy, validate_strategy, StrategyDSL, PRESETS
        )
        from dataclasses import asdict

        # Allow preset reference: {"preset_id": 0}
        if "preset_id" in strategy:
            pid = int(strategy["preset_id"])
            if pid < 0 or pid >= len(PRESETS):
                return envelope(error={"code": "BAD_PRESET", "message": "invalid preset_id"})
            dsl = PRESETS[pid]
        else:
            dsl = StrategyDSL(
                name=strategy.get("name", "custom"),
                weights=strategy.get("weights", {}),
                filters=strategy.get("filters", {}),
                description=strategy.get("description", ""),
            )
        errs = validate_strategy(dsl)
        if errs:
            return envelope(error={"code": "INVALID_DSL", "message": "; ".join(errs)})
        rows = run_strategy(dsl, top_n=strategy.get("top_n", 30))
        return envelope(data={
            "strategy": asdict(dsl),
            "rows": [asdict(r) for r in rows],
            "count": len(rows),
        })
    except Exception as e:
        logger.exception("strategy/run failed")
        return envelope(error={"code": "STRATEGY_FAIL", "message": str(e)[:200]})


@router.get("/strategy/presets")
def get_strategy_presets() -> dict[str, Any]:
    """미리 정의된 전략 리스트."""
    try:
        from ky_core.scoring.alpha_library import PRESETS
        from dataclasses import asdict
        return envelope(data={
            "presets": [{"id": i, **asdict(p)} for i, p in enumerate(PRESETS)],
        })
    except Exception as e:
        return envelope(error={"code": "PRESETS_FAIL", "message": str(e)[:200]})


@router.get("/leverage/backtest")
def get_leverage_backtest(z_threshold: float = Query(1.0)) -> dict[str, Any]:
    """KODEX 레버리지/인버스/안전 회전 전략 백테스트."""
    try:
        from ky_core.scoring.leverage_strategy import run_leverage_backtest
        from dataclasses import asdict
        return envelope(data=asdict(run_leverage_backtest(z_threshold=z_threshold)))
    except Exception as e:
        return envelope(error={"code": "LEVERAGE_FAIL", "message": str(e)[:200]})


@router.get("/global/mapping")
def get_global_mapping() -> dict[str, Any]:
    """WICS ↔ GICS 매핑 (글로벌 매트릭스 1차)."""
    try:
        from ky_core.scoring.global_matrix import get_global_mapping
        from dataclasses import asdict
        rows = get_global_mapping()
        return envelope(data={"rows": [asdict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return envelope(error={"code": "GLOBAL_MAPPING_FAIL", "message": str(e)[:200]})


@router.get("/forecast")
def get_forecast() -> dict[str, Any]:
    """ML baseline — sector ETF next-quarter return forecast (OLS)."""
    try:
        from ky_core.scoring.ml_forecast import forecast_next_quarter
        from dataclasses import asdict
        rows = forecast_next_quarter()
        return envelope(data={"rows": [asdict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        logger.exception("forecast failed")
        return envelope(error={"code": "FORECAST_FAIL", "message": str(e)[:200]})


@router.get("/cycle")
def get_cycle(n_clusters: int = Query(4, ge=2, le=8)) -> dict[str, Any]:
    """매크로 사이클 클러스터링 (K-means)."""
    try:
        from ky_core.scoring.macro_cycle import compute_macro_cycle
        from dataclasses import asdict
        result = compute_macro_cycle(n_clusters=n_clusters)
        return envelope(data={
            "n_clusters": result.n_clusters,
            "n_quarters": result.n_quarters,
            "n_features": result.n_features,
            "label_for_current": result.label_for_current,
            "regimes": [asdict(r) for r in result.regimes],
            "snapshots": [asdict(s) for s in result.snapshots[-40:]],  # last 10 yrs
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("cycle compute failed")
        return envelope(error={"code": "CYCLE_FAIL", "message": str(e)[:200]})


@router.get("/alerts")
def get_alerts(
    z_threshold: float = Query(2.0, ge=1.0, le=4.0),
    method: str = Query("yoy", pattern="^(yoy|raw)$"),
) -> dict[str, Any]:
    """매트릭스 alert — |z| ≥ threshold cells."""
    try:
        from ky_core.scoring.alerts import compute_alerts
        from dataclasses import asdict
        alerts = compute_alerts(z_threshold=z_threshold, method=method)
        return envelope(data={
            "alerts": [asdict(a) for a in alerts],
            "count": len(alerts),
            "z_threshold": z_threshold,
            "method": method,
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("alerts compute failed")
        return envelope(error={"code": "ALERTS_FAIL", "message": str(e)[:200]})


_BRIEFING_CACHE: dict[str, Any] | None = None
_BRIEFING_TS: float = 0.0
_BRIEFING_TTL = 600.0  # 10 min


@router.get("/briefing")
def get_briefing(force: bool = Query(False)) -> dict[str, Any]:
    """일일 브리핑 — 매트릭스 + 강도 + 알림 + 사이클 + 주도주 한 페이지 요약. 10분 캐시."""
    global _BRIEFING_CACHE, _BRIEFING_TS
    now = time.time()
    if not force and _BRIEFING_CACHE and (now - _BRIEFING_TS) < _BRIEFING_TTL:
        return envelope(data=_BRIEFING_CACHE)
    try:
        from ky_core.scoring.briefing import generate_briefing
        from dataclasses import asdict
        b = generate_briefing()
        body = {
            "as_of": b.as_of,
            "headline": b.headline,
            "cycle_label": b.cycle_label,
            "sections": [asdict(s) for s in b.sections],
            "full_markdown": b.full_markdown,
        }
        _BRIEFING_CACHE = body
        _BRIEFING_TS = now
        return envelope(data=body)
    except Exception as e:
        logger.exception("briefing failed")
        return envelope(error={"code": "BRIEFING_FAIL", "message": str(e)[:200]})


@router.get("/backtest")
def get_backtest(
    top_n: int = Query(3, ge=1, le=10),
    short_bottom: bool = Query(False),
    method: str = Query("detrended", pattern="^(detrended|raw)$"),
    period_start: str = Query("2015-01"),
) -> dict[str, Any]:
    """섹터 로테이션 백테스트 — strength TOP-N long ± BOTTOM-N short."""
    try:
        from ky_core.scoring.sector_rotation_backtest import run_sector_rotation_backtest
        result = run_sector_rotation_backtest(
            top_n=top_n,
            short_bottom=short_bottom,
            macro_method=method,
            period_start=period_start,
        )
        return envelope(data=result.to_dict())
    except Exception as e:
        logger.exception("backtest failed")
        return envelope(error={"code": "BACKTEST_FAIL", "message": str(e)[:200]})


@router.get("/leadlag")
def get_leadlag(
    method: str = Query("yoy", pattern="^(yoy|raw)$"),
    min_corr: float = Query(0.3, ge=0.1, le=0.9),
) -> dict[str, Any]:
    """섹터 lead-lag 네트워크 — 페어별 cross-correlation."""
    try:
        from ky_core.scoring.sector_leadlag import (
            compute_pair_correlations, compute_sector_lead_scores)
        pairs = compute_pair_correlations(method=method, min_corr=min_corr)
        scores = compute_sector_lead_scores(pairs=pairs, method=method)
        return envelope(data={
            "pairs": [asdict_safe(p) for p in pairs],
            "lead_scores": [asdict_safe(s) for s in scores],
            "n_pairs": len(pairs),
            "method": method,
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("leadlag compute failed")
        return envelope(error={"code": "LEADLAG_FAIL", "message": str(e)[:200]})


def asdict_safe(obj: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass
    if is_dataclass(obj):
        return asdict(obj)
    return dict(obj.__dict__) if hasattr(obj, '__dict__') else {}


@router.get("/leaders")
def get_leader_stocks(
    method: str = Query("detrended", pattern="^(detrended|raw)$"),
    top_n: int = Query(50, ge=1, le=300),
    min_filled: int = Query(5, ge=1, le=10),
) -> dict[str, Any]:
    """Macro-aware leader stock ranking. 종목 단위 점수 = sector 매크로 강도
    × (가격 RS placeholder 0.5) × (펀더 placeholder 0.5)."""
    try:
        from ky_core.scoring.leader_stock import scan_leader_stocks
        rows = scan_leader_stocks(macro_method=method, min_filled=min_filled, top_n=top_n)
        return envelope(data={
            "rows": [r.to_dict() for r in rows],
            "count": len(rows),
            "method": method,
            "top_n": top_n,
            "as_of": time.strftime("%Y-%m-%d %H:%M"),
        })
    except Exception as e:
        logger.exception("leaders compute failed")
        return envelope(error={"code": "LEADERS_FAIL", "message": str(e)[:200]})


@router.get("/symbols")
def get_symbols(sector: str = Query(..., description="WICS sector_num like '02'"),
                limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """WICS sector → ky.db universe ticker list."""
    sec = next((s for s in WICS_SECTORS if s[0] == sector), None)
    if not sec:
        raise HTTPException(status_code=404, detail=f"sector={sector} not found")
    sec_num, slug, label, univ_list, etf = sec

    if not DB_PATH.exists():
        return envelope(data={"sector": {"num": sec_num, "slug": slug, "label": label},
                              "tickers": [], "count": 0, "note": "ky.db not present"})

    placeholders = ",".join(["?"] * len(univ_list))
    sql = f"""
        SELECT ticker, name, market, sector, industry, is_etf
        FROM universe
        WHERE sector IN ({placeholders}) AND is_etf = 0
        ORDER BY ticker
        LIMIT ?
    """
    out = []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        for row in con.execute(sql, [*univ_list, limit]).fetchall():
            out.append({
                "ticker": row["ticker"],
                "name": row["name"],
                "market": row["market"],
                "sector": row["sector"],
                "industry": row["industry"],
            })
        con.close()
    except Exception as e:
        logger.warning("symbols query failed: %s", e)
        return envelope(error={"code": "DB_ERROR", "message": str(e)[:200]})

    return envelope(data={
        "sector": {"num": sec_num, "slug": slug, "label": label,
                   "universe_match": univ_list, "primary_etf": etf},
        "tickers": out,
        "count": len(out),
    })
