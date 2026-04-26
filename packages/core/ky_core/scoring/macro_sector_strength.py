"""Macro sector strength — WICS 34섹터 × 10지표 매트릭스 기반 섹터 강도.

가격 기반 sector_strength (RS/등락률) 와 분리. 이 모듈은:
  - 각 cell의 3Y z-score (또는 12M%) 를 활용
  - 'leadlag' (선/동/후) 가중치 적용 가능
  - 섹터별 aggregate score = 평균 z-score (정렬: 높을수록 강세)
  - 정상 단계: 매트릭스 풍부도 (filled cell 수)도 가중

API: ``compute_macro_strength()`` → list[SectorMacroStrength] (강도 desc).

호출 비용: 매트릭스 endpoint와 동일한 disk 스캔. 이미 sector_indicators API
캐시(5분)가 있으므로, 그 캐시를 재사용하지 않고 직접 disk 스캔하더라도 비싸지 않음.

이 모듈은 routers/research/sector_indicators.py 의 ``compute_strength``
endpoint에서 재호출됨. 향후 ``scoring/leader_stock.py`` 가 이 점수를 입력으로
받아 종목 단위 점수로 결합.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

DATA_ROOT = Path.home() / ".gazua" / "data" / "sectors"
MAP_PATH = (
    Path(__file__).resolve().parents[4] / "docs" / "SECTOR_INDICATOR_MAP.md"
)


# --------------------------------------------------------------------------- #
# Lightweight cell parser (mirrors routers.research.sector_indicators)        #
# --------------------------------------------------------------------------- #

_SECTOR_RE = re.compile(r"^##\s*\[(\d{2})\]\s+(.+?)\s*$", re.M)
_ROW_RE = re.compile(
    r"^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
    re.M,
)


def _parse_map() -> list[dict[str, Any]]:
    text = MAP_PATH.read_text(encoding="utf-8")
    matches = list(_SECTOR_RE.finditer(text))
    out: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        chunk = text[m.end():(matches[i + 1].start() if i + 1 < len(matches) else len(text))]
        for row in _ROW_RE.findall(chunk):
            idx, indicator, source, category, leadlag = row
            out.append({
                "cell_id": f"{m.group(1)}_{idx.zfill(2)}",
                "sector_num": m.group(1),
                "sector_name": m.group(2).strip(),
                "idx": int(idx),
                "indicator": indicator.strip(),
                "source_raw": source.strip(),
                "category": category.strip(),
                "leadlag": leadlag.strip(),
            })
    return out


# --------------------------------------------------------------------------- #
# Cell value extraction (latest + z-score)                                    #
# --------------------------------------------------------------------------- #

# Heuristic per-folder column mapping (matches sector_indicators.py)
_VAL_COLS = {
    "fred": ("date", "value"),
    "imported/fred": ("date", "value"),
    "ecos": ("date", "value"),
    "imported/ecos": ("date", "value"),
    "kosis": ("PRD_DE", "DT"),
    "kis_index": ("stck_bsop_date", "bstp_nmix_prpr"),
    "yf_commodities": ("date", "close"),
    "eia": ("date", "value"),
    "oecd": ("period", "value"),
    "worldbank": ("date", "value"),
    "cftc": ("report_date", "managed_money_long"),
    "un_comtrade": ("period", "trade_value_usd"),
    "pytrends": ("date", "value"),
    "openfda": ("month", "approvals"),
    "customs": ("period", "export_usd"),
    "stooq": ("date", "close"),
}

# Sector tag prefixes for matching CSV slug to WICS sector
_SECTOR_PREFIXES: dict[str, list[str]] = {
    "01": ["energy", "oil"], "02": ["chemical", "oil"], "03": ["nonferrous"],
    "04": ["steel"], "05": ["construction"], "06": ["machinery"],
    "07": ["ship"], "08": ["trade", "retail"], "09": ["transport"],
    "10": ["auto"], "11": ["cosmetic", "retail"], "12": ["tourism"],
    "13": ["media"], "14": ["retail"], "15": ["food", "retail"],
    "16": ["food"], "17": ["pharma"], "18": ["bank"], "19": ["broker"],
    "20": ["insurance"], "21": ["fincap", "bank"], "22": ["software"],
    "23": ["itservice", "software"], "24": ["display"], "25": ["handset"],
    "26": ["semiconductor"], "27": ["itappliance", "electronics"],
    "28": ["electric", "electronics"], "29": ["telecom"], "30": ["utility"],
    "31": ["realestate", "bank"], "32": ["defense"], "33": ["paper"],
    "34": ["electronics", "itappliance", "electric"],
}

_SOURCE_FOLDERS = {
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


def _disk_index() -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []

    def walk(d: Path, prefix: str = "") -> None:
        try:
            for c in d.iterdir():
                if c.is_dir():
                    walk(c, prefix + c.name + "/")
                elif c.suffix == ".csv":
                    try:
                        n = sum(1 for _ in c.open(encoding="utf-8", errors="ignore")) - 1
                    except Exception:
                        n = 0
                    out.append((prefix.rstrip("/"), c.stem, max(n, 0)))
        except Exception:
            pass

    if DATA_ROOT.exists():
        walk(DATA_ROOT)
    return out


def _resolve_cell(cell: dict, csvs: list[tuple[str, str, int]]) -> tuple[str, str] | None:
    src_folders = []
    for tok, fs in _SOURCE_FOLDERS.items():
        if tok in cell["source_raw"]:
            src_folders.extend(fs)
    sec_prefs = _SECTOR_PREFIXES.get(cell["sector_num"], [])

    # 1. sector tag prefix in source folder
    for tag in sec_prefs:
        for fld, slug, rows in csvs:
            if rows < 100:
                continue
            sl = slug.lower()
            if not (sl.startswith(tag + "__") or sl.startswith(tag + "_")):
                continue
            if not src_folders or fld in src_folders or any(fld.startswith(sf) for sf in src_folders):
                return (fld, slug)

    # 2. source-only fallback (KOSIS/ECOS/FRED/EIA folder presence)
    for tok in ["KOSIS", "ECOS", "FRED", "EIA"]:
        if tok in cell["source_raw"]:
            for fld, slug, rows in csvs:
                if any(fld == f or fld.startswith(f) for f in _SOURCE_FOLDERS[tok]) and rows >= 100:
                    return (fld, slug)
    return None


def _read_values(folder: str, slug: str) -> list[float]:
    path = DATA_ROOT / folder / f"{slug}.csv"
    if not path.exists():
        return []
    _date_col, val_col = _VAL_COLS.get(folder, ("date", "value"))
    out: list[float] = []
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                v = row.get(val_col)
                if v is None or v == "":
                    continue
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    continue
    except Exception:
        return []
    return out


def _zscore(values: list[float]) -> float | None:
    """Raw level z-score (참고용 — 인플레 데이터에서 bias 강함)."""
    import math
    if not values:
        return None
    clean = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(clean) < 12:
        return None
    sample = clean[-min(len(clean), 756):]
    mean = sum(sample) / len(sample)
    var = sum((x - mean) ** 2 for x in sample) / len(sample)
    std = var ** 0.5
    if std == 0 or not math.isfinite(std):
        return None
    latest = sample[-1]
    z = (latest - mean) / std
    return z if math.isfinite(z) else None


def _yoy_change(values: list[float]) -> list[float]:
    """Year-over-year % change. window=12 (월) / 252 (일) / 4 (분기) 자동 추정."""
    import math
    n = len(values)
    if n < 26:
        return []
    # Heuristic: rows>1500 → daily(252); 30~1500 → monthly(12); else quarterly(4)
    if n > 1500:
        win = 252
    elif n > 100:
        win = 12
    else:
        win = 4
    out: list[float] = []
    for i in range(win, n):
        prev = values[i - win]
        cur = values[i]
        if isinstance(prev, (int, float)) and isinstance(cur, (int, float)) \
                and math.isfinite(prev) and math.isfinite(cur) and prev != 0:
            out.append((cur - prev) / abs(prev) * 100.0)
    return out


def _zscore_detrended(values: list[float]) -> float | None:
    """변화율 기반 z-score (1차 차분 + YoY% 평균).

    Steps:
    1) Compute YoY % change series — removes level/inflation bias.
    2) Apply z-score on the change series — measures momentum vs history.

    인플레 데이터 (KOSIS 광공업 명목값 등)에서도 의미 있는 강도 신호 산출.
    """
    import math
    yoy = _yoy_change(values)
    if len(yoy) < 12:
        return None
    sample = yoy[-min(len(yoy), 756):]
    mean = sum(sample) / len(sample)
    var = sum((x - mean) ** 2 for x in sample) / len(sample)
    std = var ** 0.5
    if std == 0 or not math.isfinite(std):
        return None
    latest = sample[-1]
    z = (latest - mean) / std
    return z if math.isfinite(z) else None


# --------------------------------------------------------------------------- #
# Aggregate to sector score                                                   #
# --------------------------------------------------------------------------- #

# Lead/Lag weight — 선행 1.5x, 동행 1.0x, 후행 0.5x.
_LEADLAG_WEIGHT = {
    "선행": 1.5, "선행 (역)": 1.5, "선행 (역신호)": 1.5,
    "동행": 1.0, "동행 (역)": 1.0,
    "후행": 0.5, "후행 (역)": 0.5,
}


def _leadlag_w(s: str) -> float:
    s = (s or "").strip()
    for k, w in _LEADLAG_WEIGHT.items():
        if s.startswith(k):
            return w
    return 1.0


@dataclass
class SectorMacroStrength:
    rank: int
    sector_num: str
    sector_name: str
    score: float           # weighted mean z-score
    n_filled: int          # # of cells with usable z
    n_total: int           # total cells (10)
    fill_pct: float
    top_signals: list[dict[str, Any]]   # 3 strongest indicators
    bottom_signals: list[dict[str, Any]]  # 3 weakest indicators

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_macro_strength(method: str = "detrended") -> list[SectorMacroStrength]:
    """매트릭스 데이터로부터 sector aggregate score 계산.

    Args:
        method: "detrended" (default, YoY% 변화율 z-score — 인플레 bias 제거)
                "raw" (level z-score — 참고용)

    Sector score = sum(weight * z) / sum(weight) over filled cells.
    Lead/lag 가중치 (선행 1.5, 동행 1.0, 후행 0.5).
    Z-score는 지표 본래 방향 그대로 (역신호는 leadlag에 '역' 포함 시 부호 반전).
    """
    cells = _parse_map()
    csvs = _disk_index()
    z_func = _zscore_detrended if method == "detrended" else _zscore

    # Sector → list of {z, weight, indicator, leadlag}
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for c in cells:
        match = _resolve_cell(c, csvs)
        if not match:
            continue
        folder, slug = match
        values = _read_values(folder, slug)
        z = z_func(values)
        if z is None:
            continue
        # 역신호 처리 (선행 (역) 등): leadlag 문자열에 '(역)' 있으면 부호 반전
        ll = c["leadlag"]
        if "역" in ll:
            z = -z
        w = _leadlag_w(ll)
        by_sector.setdefault(c["sector_num"], []).append({
            "z": z, "weight": w, "indicator": c["indicator"],
            "leadlag": ll, "category": c["category"],
            "cell_id": c["cell_id"], "sector_name": c["sector_name"],
        })

    # All sector_nums (even those with 0 filled)
    sector_meta = {c["sector_num"]: c["sector_name"] for c in cells}
    sector_total_cells: dict[str, int] = {}
    for c in cells:
        sector_total_cells[c["sector_num"]] = sector_total_cells.get(c["sector_num"], 0) + 1

    results: list[SectorMacroStrength] = []
    for sec_num, sec_name in sector_meta.items():
        signals = by_sector.get(sec_num, [])
        n_filled = len(signals)
        n_total = sector_total_cells.get(sec_num, 10)
        if n_filled == 0:
            score = 0.0
        else:
            num = sum(s["z"] * s["weight"] for s in signals)
            den = sum(s["weight"] for s in signals)
            score = num / den if den > 0 else 0.0
        # Top/bottom 3 by raw z
        sorted_sig = sorted(signals, key=lambda s: s["z"], reverse=True)
        top = [{"indicator": s["indicator"], "z": round(s["z"], 2),
                "leadlag": s["leadlag"], "cell_id": s["cell_id"]}
               for s in sorted_sig[:3]]
        bottom = [{"indicator": s["indicator"], "z": round(s["z"], 2),
                   "leadlag": s["leadlag"], "cell_id": s["cell_id"]}
                  for s in sorted_sig[-3:][::-1]]
        results.append(SectorMacroStrength(
            rank=0,
            sector_num=sec_num,
            sector_name=sec_name,
            score=round(score, 3),
            n_filled=n_filled,
            n_total=n_total,
            fill_pct=round(n_filled * 100.0 / max(1, n_total), 1),
            top_signals=top,
            bottom_signals=bottom,
        ))

    # Sort by score desc and assign rank
    results.sort(key=lambda r: r.score, reverse=True)
    for i, r in enumerate(results, 1):
        r.rank = i
    return results
