"""Global matrix — US 1차 (GICS 11 sectors × 매크로).

미국 GICS 11 sectors:
  XLE (Energy), XLB (Materials), XLI (Industrials), XLY (Cons Disc),
  XLP (Cons Staples), XLV (Health Care), XLF (Financials),
  XLK (Tech), XLC (Comm Svc), XLU (Utilities), XLRE (Real Estate)

매크로 cells (FRED 위주 — 한국 매트릭스와 부분 공통):
  - DGS10, T10Y2Y, FEDFUNDS, VIXCLS, INDPRO, RSAFS, UMCSENT, HOUST, NEWORDER, CPILFESL

향후 yfinance 어댑터로 GICS ETF 가격 추가 + cross-country lead-lag 분석.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

GICS_SECTORS = [
    ("01", "XLE",  "Energy"),
    ("02", "XLB",  "Materials"),
    ("03", "XLI",  "Industrials"),
    ("04", "XLY",  "Consumer Discretionary"),
    ("05", "XLP",  "Consumer Staples"),
    ("06", "XLV",  "Health Care"),
    ("07", "XLF",  "Financials"),
    ("08", "XLK",  "Technology"),
    ("09", "XLC",  "Communication Services"),
    ("10", "XLU",  "Utilities"),
    ("11", "XLRE", "Real Estate"),
]

# WICS ↔ GICS rough mapping (cross-country lead-lag 분석용)
WICS_TO_GICS = {
    "01": "01",  # 에너지 → Energy
    "02": "02",  # 화학 → Materials
    "03": "02",  # 비철금속 → Materials
    "04": "02",  # 철강 → Materials
    "05": "03",  # 건설 → Industrials
    "06": "03",  # 기계 → Industrials
    "07": "03",  # 조선 → Industrials
    "08": "03",  # 상사·자본재 → Industrials
    "09": "03",  # 운송 → Industrials
    "10": "04",  # 자동차 → Consumer Discretionary
    "11": "04",  # 화장품·의류 → Consumer Discretionary
    "12": "04",  # 호텔·레저 → Consumer Discretionary
    "13": "09",  # 미디어 → Communication Services
    "14": "04",  # 유통 → Consumer Discretionary
    "15": "05",  # 필수소비재 → Consumer Staples
    "16": "05",  # 식품·담배 → Consumer Staples
    "17": "06",  # 제약·바이오 → Health Care
    "18": "07",  # 은행 → Financials
    "19": "07",  # 증권 → Financials
    "20": "07",  # 보험 → Financials
    "21": "07",  # 다각화 금융 → Financials
    "22": "08",  # 소프트웨어 → Tech
    "23": "08",  # IT 서비스 → Tech
    "24": "08",  # 디스플레이 → Tech
    "25": "08",  # 핸드셋 → Tech
    "26": "08",  # 반도체 → Tech
    "27": "04",  # IT 가전 → Consumer Discretionary
    "28": "03",  # 전기장비 → Industrials
    "29": "09",  # 통신 서비스 → Communication Services
    "30": "10",  # 유틸리티 → Utilities
    "31": "11",  # 부동산 → Real Estate
    "32": "03",  # 우주항공·방위 → Industrials
    "33": "02",  # 종이·목재 → Materials
    "34": "08",  # 일반 전기전자 → Tech
}


@dataclass
class GlobalSectorMap:
    wics_num: str
    wics_name: str
    gics_num: str
    gics_etf: str
    gics_name: str


def get_global_mapping() -> list[GlobalSectorMap]:
    """WICS → GICS 매핑 테이블."""
    from ky_core.scoring.macro_sector_strength import _parse_map
    cells = _parse_map()
    wics_names = {c["sector_num"]: c["sector_name"] for c in cells}
    gics_lookup = {g[0]: (g[1], g[2]) for g in GICS_SECTORS}

    out = []
    for wics_num, wics_name in sorted(wics_names.items()):
        gics_num = WICS_TO_GICS.get(wics_num)
        if not gics_num:
            continue
        etf, gics_name = gics_lookup.get(gics_num, ("?", "?"))
        out.append(GlobalSectorMap(
            wics_num=wics_num, wics_name=wics_name,
            gics_num=gics_num, gics_etf=etf, gics_name=gics_name,
        ))
    return out


# Future: implement collect_us_matrix() to fetch GICS ETF prices via yfinance
# + FRED macro → build US matrix mirror of WICS structure.
# This stub establishes the schema; real fetch added in next iteration.
