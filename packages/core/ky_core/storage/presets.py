"""Daily collection presets.

These are opinionated defaults — a curated set of macro series that the
platform expects to refresh every day (and that we backfill 10 years for on
first-run). The runner script reads these when called with
``--source all-macro``.

Each entry line documents the source, series identifier, frequency and a
human-readable label so the bulk-backfill log is self-explaining.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# --------------------------------------------------------------------------- #
# FRED — U.S. macro                                                           #
# --------------------------------------------------------------------------- #
# Growth / inflation / liquidity / credit must all be covered so that
# compute_compass() (packages/core/ky_core/macro/compass.py) has at least one
# series per axis. BAA10Y/AAA10Y drive the credit axis. DEXKOUS is the
# daily KRW/USD noon buying rate — it exists here (and in ECOS) as the
# authoritative FX fallback for when the EXIM adapter is auth-gated.
FRED_SERIES: list[str] = [
    "GDP",
    "CPIAUCSL",
    "UNRATE",
    "FEDFUNDS",
    "DGS10",
    "DGS2",
    "DFF",
    "BAA10Y",
    "AAA10Y",
    "DEXKOUS",
]

# --------------------------------------------------------------------------- #
# ECOS — Bank of Korea                                                        #
# --------------------------------------------------------------------------- #
# Each row: (stat_code, item_code, freq, description).
# Every code here has been validated against the live ECOS API as of 2026-04.
# Covers: policy rate · FX · inflation (CPI/core/PPI) · money (M1/M2) ·
# growth (GDP expenditure) · production · employment · leading indicators ·
# trade · housing · government bond yields. Together these give the macro
# compass a full Korean counterpart to the FRED-only coverage it had before.
ECOS_SERIES: list[tuple[str, str, str, str]] = [
    # --- rates & FX (daily) ---
    ("722Y001", "0101000", "D", "기준금리 (BoK base rate)"),
    ("731Y001", "0000001", "D", "원/달러 환율 (KRW/USD)"),
    ("817Y002", "010190000", "D", "국고채 3년 수익률"),
    ("817Y002", "010195000", "D", "국고채 10년 수익률"),
    ("817Y002", "010200000", "D", "국고채 20년 수익률"),
    # --- inflation (monthly) ---
    ("901Y009", "0",  "M", "CPI 총지수"),
    ("901Y010", "DB", "M", "근원 CPI (식료품·에너지 제외)"),
    ("901Y010", "QB", "M", "근원 CPI (농산물·석유류 제외)"),
    ("404Y014", "*AA", "M", "생산자물가지수 (PPI)"),
    # --- money & liquidity (monthly) ---
    ("161Y006", "BBHA00", "M", "M2 평잔 원계열"),
    ("161Y002", "BBLA00", "M", "M1 평잔 원계열"),
    # --- real activity (monthly) ---
    ("901Y033", "A00",   "M", "전산업생산지수"),
    ("901Y027", "I61BA", "M", "취업자수"),
    ("901Y027", "I61B",  "M", "경제활동인구"),
    ("901Y067", "I16A",  "M", "경기선행종합지수"),
    ("901Y067", "I16B",  "M", "경기동행종합지수"),
    ("403Y001", "*AA",   "M", "수출액"),
    # --- housing (monthly) ---
    ("901Y062", "P63A", "M", "주택매매가격지수"),
    ("901Y063", "P64A", "M", "주택전세가격지수"),
    # --- growth (quarterly, seasonally adjusted) ---
    ("200Y108", "10601", "Q", "실질 GDP (지출측, 계절조정, 분기)"),
]

# --------------------------------------------------------------------------- #
# KOSIS — Statistics Korea                                                    #
# --------------------------------------------------------------------------- #
# 10 curated macro tables. Each entry was validated against the live KOSIS
# OpenAPI on 2026-04-22:
#   1. Hit https://kosis.kr/openapi/statisticsMeta.do type=ITM to get itmId
#   2. Hit /Param/statisticsParameterData.do with ``objL1=ALL`` (the KOSIS
#      wildcard that returns every objL1 bucket) to confirm data flows.
# Tables that require a specific objL1 code (sectoral BSI, service sub-index
# by region) are intentionally omitted — the ``ALL`` contract is the one the
# collect-runner depends on.
#
# Schema per row — the runner (scripts/collect.py :: collect_kosis) reads:
#   org_id, tbl_id, itm_id, obj_l1, prd_se, series_id, description
KOSIS_SERIES: list[dict[str, Any]] = [
    {
        "org_id": "101", "tbl_id": "DT_1JH20201", "itm_id": "T1",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1JH20201/T1/ALL",
        "description": "전산업생산지수 (원지수)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1K41013", "itm_id": "T1",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1K41013/T1/ALL",
        "description": "소매업태별 판매액지수 (2020=100)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1KC2020", "itm_id": "T1",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1KC2020/T1/ALL",
        "description": "업종별 서비스업생산지수 (2020=100)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1C8015", "itm_id": "T1",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1C8015/T1/ALL",
        "description": "경기종합지수 (10차)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1C8016", "itm_id": "T1",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1C8016/T1/ALL",
        "description": "경기종합지수 시계열 (10차)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1DA7001S", "itm_id": "T10",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1DA7001S/T10/ALL",
        "description": "성별 경제활동인구 총괄 (15세이상 인구)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1DA7011S", "itm_id": "T30",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1DA7011S/T30/ALL",
        "description": "연령계층별 취업자",
    },
    {
        "org_id": "101", "tbl_id": "DT_1YL7001", "itm_id": "T10",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1YL7001/T10/ALL",
        "description": "수입액 (시도)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1YL1701", "itm_id": "T10",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1YL1701/T10/ALL",
        "description": "주택매매가격변동률 (시도/시/군/구)",
    },
    {
        "org_id": "101", "tbl_id": "DT_1G18011", "itm_id": "T10",
        "obj_l1": "ALL", "prd_se": "M",
        "series_id": "kosis/DT_1G18011/T10/ALL",
        "description": "공사별 건설기성액",
    },
]

# --------------------------------------------------------------------------- #
# EIA — U.S. energy                                                           #
# --------------------------------------------------------------------------- #
# The v2 REST API serves these as path + series facet rather than
# ``seriesid/{id}/data`` (which 404s for petroleum weeklies). We therefore
# carry (path, series_code, description) triples so the adapter can build the
# right URL.
EIA_SERIES: list[tuple[str, str, str]] = [
    ("petroleum/stoc/wstk", "WCESTUS1", "U.S. crude oil ending stocks (weekly, excl. SPR)"),
    ("petroleum/pnp/wiup",  "WPULEUS3", "U.S. refinery utilisation (%, weekly)"),
    ("natural-gas/stor/wkly", "NW2_EPG0_SWO_R48_BCF", "U.S. natural gas working storage (BCF, weekly)"),
]


def default_window(days: int = 365) -> tuple[str, str]:
    """ISO (start, end) window for macro backfills."""
    today = date.today()
    start = today - timedelta(days=days)
    return start.isoformat(), today.isoformat()


def yyyymmdd_window(days: int = 365) -> tuple[str, str]:
    today = date.today()
    start = today - timedelta(days=days)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


# A single bundle the runner can consume.
MACRO_DAILY_PRESET: dict[str, Any] = {
    "fred": FRED_SERIES,
    "ecos": ECOS_SERIES,
    "kosis": KOSIS_SERIES,
    "eia": EIA_SERIES,
    "exim": [date.today().isoformat()],
}


# --------------------------------------------------------------------------- #
# MACRO_TIER_A — fallback chains for the 200-indicator architecture           #
# --------------------------------------------------------------------------- #
# Aligns ky-platform with the QuantDB "track1 data intelligence" design
# (docs/track1_data_intelligence_200_indicator_architecture.md, 2026-02-26).
#
# Each key is a *logical indicator* — a stable name the compass/regime
# modules read ("KR_CPI", "US_10Y") — and each value is an ordered fallback
# chain ``[(source_id, *identifier parts)]`` where the first tuple that
# produces observations wins. Identifier parts are source-specific:
#
#   - ("fred",  series_id)                           — FRED series
#   - ("ecos",  stat_code, item_code[, freq])        — ECOS API
#   - ("kosis", tbl_id[, itm_id, obj_l1, prd_se])    — KOSIS API
#   - ("exim", )                                     — EXIM (single FX row)
#   - ("eia",   path, series_code)                   — EIA v2 path endpoint
#   - ("derived", formula_id, *inputs)               — computed in-process
#
# The chain is resolved by :func:`ky_core.macro.pickers.pick_indicator` which
# hits the Repository once per tuple and returns the first non-empty series.
# This means every critical indicator has explicit redundancy — when FRED
# throttles or KOSIS drops a table, the compass still computes.
#
# Coverage note (2026-04-22): we enumerate 60+ indicators here to seed the
# registry. Not every chain has stored observations yet; the picker returns
# None for unresolved entries and the caller reports the gap. Fill the gaps
# by running ``python scripts/collect.py --source all-macro``.

MACRO_TIER_A: dict[str, list[tuple[str, ...]]] = {
    # ---- FX / currencies -------------------------------------------------
    "USDKRW": [
        ("ecos", "731Y001", "0000001", "D"),
        ("fred", "DEXKOUS"),
        ("exim",),
    ],
    "USDEUR": [("fred", "DEXUSEU")],
    "USDJPY": [("fred", "DEXJPUS")],
    "USDCNY": [("fred", "DEXCHUS")],
    "DXY": [("fred", "DTWEXBGS"), ("fred", "DTWEXM")],

    # ---- KR policy & rates ----------------------------------------------
    "KR_BASE_RATE": [("ecos", "722Y001", "0101000", "D")],
    "KR_3Y_TREASURY": [("ecos", "817Y002", "010190000", "D")],
    "KR_10Y_TREASURY": [("ecos", "817Y002", "010195000", "D")],
    "KR_20Y_TREASURY": [("ecos", "817Y002", "010200000", "D")],

    # ---- US policy & rates ----------------------------------------------
    "US_FEDFUNDS": [("fred", "FEDFUNDS"), ("fred", "DFF")],
    "US_2Y": [("fred", "DGS2")],
    "US_10Y": [("fred", "DGS10"), ("fred", "GS10")],
    "US_30Y": [("fred", "DGS30"), ("fred", "GS30")],
    "US_3M": [("fred", "DGS3MO"), ("fred", "TB3MS")],

    # ---- US credit spreads ----------------------------------------------
    "BAA_10Y_SPREAD": [("fred", "BAA10YM"), ("fred", "BAA10Y")],
    "AAA_10Y_SPREAD": [("fred", "AAA10YM"), ("fred", "AAA10Y")],
    "HY_OAS": [("fred", "BAMLH0A0HYM2")],
    "IG_OAS": [("fred", "BAMLC0A0CM")],

    # ---- KR inflation ---------------------------------------------------
    "KR_CPI": [
        ("ecos", "901Y009", "0", "M"),
        ("kosis", "DT_1J22001", "T1", "ALL", "M"),
    ],
    "KR_CORE_CPI": [
        ("ecos", "901Y010", "DB", "M"),
        ("ecos", "901Y010", "QB", "M"),
    ],
    "KR_PPI": [("ecos", "404Y014", "*AA", "M")],

    # ---- US inflation ---------------------------------------------------
    "US_CPI": [("fred", "CPIAUCSL"), ("fred", "CPILFESL")],
    "US_CORE_CPI": [("fred", "CPILFESL")],
    "US_PCE": [("fred", "PCEPI")],
    "US_CORE_PCE": [("fred", "PCEPILFE")],
    "US_PPI": [("fred", "PPIACO"), ("fred", "PPIFIS")],

    # ---- KR money & liquidity ------------------------------------------
    "KR_M2": [("ecos", "161Y006", "BBHA00", "M")],
    "KR_M1": [("ecos", "161Y002", "BBLA00", "M")],

    # ---- US money & liquidity ------------------------------------------
    "US_M2": [("fred", "M2SL"), ("fred", "WM2NS")],
    "US_M1": [("fred", "M1SL")],

    # ---- KR growth & activity -------------------------------------------
    "KR_GDP": [("ecos", "200Y108", "10601", "Q")],
    "KR_INDUSTRIAL_PRODUCTION": [
        ("ecos", "901Y033", "A00", "M"),
        ("kosis", "DT_1JH20201", "T1", "ALL", "M"),
    ],
    "KR_EMPLOYED": [
        ("ecos", "901Y027", "I61BA", "M"),
        ("kosis", "DT_1DA7001S", "T10", "ALL", "M"),
    ],
    "KR_LABOR_FORCE": [("ecos", "901Y027", "I61B", "M")],
    "KR_LEADING_INDEX": [("ecos", "901Y067", "I16A", "M")],
    "KR_COINCIDENT_INDEX": [("ecos", "901Y067", "I16B", "M")],
    "KR_RETAIL_SALES_INDEX": [("kosis", "DT_1K41013", "T1", "ALL", "M")],
    "KR_SERVICE_PRODUCTION_INDEX": [("kosis", "DT_1KC2020", "T1", "ALL", "M")],
    "KR_ECON_CYCLE_INDEX": [
        ("kosis", "DT_1C8015", "T1", "ALL", "M"),
        ("kosis", "DT_1C8016", "T1", "ALL", "M"),
    ],
    "KR_CONSTRUCTION_WORK_AMOUNT": [("kosis", "DT_1G18011", "T10", "ALL", "M")],

    # ---- US growth & activity -------------------------------------------
    "US_GDP": [("fred", "GDPC1"), ("fred", "GDP")],
    "US_INDPRO": [("fred", "INDPRO")],
    "US_PAYEMS": [("fred", "PAYEMS")],
    "US_UNRATE": [("fred", "UNRATE")],
    "US_ISM_MFG": [("fred", "NAPM"), ("fred", "NAPMPI")],
    "US_RETAIL_SALES": [("fred", "RSAFS"), ("fred", "RRSFS")],
    "US_HOUSING_STARTS": [("fred", "HOUST")],

    # ---- KR trade & housing ---------------------------------------------
    "KR_EXPORTS": [("ecos", "403Y001", "*AA", "M")],
    "KR_HOUSE_PRICE_INDEX": [("ecos", "901Y062", "P63A", "M")],
    "KR_JEONSE_INDEX": [("ecos", "901Y063", "P64A", "M")],
    "KR_HOUSE_PRICE_CHANGE": [("kosis", "DT_1YL1701", "T10", "ALL", "M")],
    "KR_IMPORTS_BY_REGION": [("kosis", "DT_1YL7001", "T10", "ALL", "M")],

    # ---- Energy (EIA weekly) --------------------------------------------
    "US_CRUDE_STOCKS": [("eia", "petroleum/stoc/wstk", "WCESTUS1")],
    "US_REFINERY_UTIL": [("eia", "petroleum/pnp/wiup", "WPULEUS3")],
    "US_NATGAS_STORAGE": [("eia", "natural-gas/stor/wkly", "NW2_EPG0_SWO_R48_BCF")],

    # ---- Placeholders for future derivation (resolved by pickers module) -
    # These are intentionally empty: they exist as canonical indicator
    # *names* the downstream code can reference before source chains are
    # wired. pick_indicator() returns None → caller falls back gracefully.
    "TERM_SPREAD_10Y_2Y": [("derived", "spread", "US_10Y", "US_2Y")],
    "CREDIT_SPREAD_BAA_AAA": [("derived", "spread", "BAA_10Y_SPREAD", "AAA_10Y_SPREAD")],
    "REAL_RATE_US": [("derived", "real_rate", "US_10Y", "US_CPI")],
    "USDKRW_ZSCORE_3M": [("derived", "zscore", "USDKRW", "90d")],
    "KR_INFLATION_MOMENTUM": [("derived", "yoy_delta", "KR_CPI")],
}


def tier_a_coverage() -> dict[str, int]:
    """Count indicators by the *primary* (index-0) source id in each chain.

    Handy for the Compass/Regime ops panel — tells you at a glance how many
    indicators each backend ends up feeding.
    """
    out: dict[str, int] = {}
    for chain in MACRO_TIER_A.values():
        if not chain:
            continue
        primary = chain[0][0]
        out[primary] = out.get(primary, 0) + 1
    return out

# --------------------------------------------------------------------------- #
# Legacy archive source_id constants                                          #
# --------------------------------------------------------------------------- #
# Used by scripts/import_legacy_macro.py when bulk-importing historical CSV
# and SQLite snapshots pulled from the pre-ky-platform projects. Keeping
# source_ids distinct from the live adapter ids (`ecos`, `fred`, `eia`, ...)
# means fresh collectors never collide with the historical backfill on the
# observations unique key (source_id, series_id, date, owner_id).
#
# All values fit inside the 32-char constraint on Observation.source_id.

LEGACY_SRC_ECOS_QP = "ecos_legacy_qp"          # QuantPlatform ECOS CSVs
LEGACY_SRC_FRED_QP = "fred_legacy_qp"          # QuantPlatform FRED rows inside ECOS CSVs
LEGACY_SRC_COMMODITY = "commodity_legacy_qp"   # QuantPlatform commodity CSVs
LEGACY_SRC_MACRO_DB = "macro_legacy_qdb"       # QuantDB quant_platform_history.db macro_series
LEGACY_SRC_KOSIS_QP = "kosis_legacy_qp"        # KOSIS rows that surface inside QuantPlatform CSVs
LEGACY_SRC_UNKNOWN = "legacy_unknown"          # fallback when the source tag is blank

LEGACY_SOURCE_PREFIX_MAP: dict[str, str] = {
    "ECOS": LEGACY_SRC_ECOS_QP,
    "FRED": LEGACY_SRC_FRED_QP,
    "KOSIS": LEGACY_SRC_KOSIS_QP,
    "FRED_PUBLIC": LEGACY_SRC_FRED_QP,
    "phase4_FRED": LEGACY_SRC_FRED_QP,
    "EIA": "eia_legacy_qdb",
    "QUANTKING_XLSB": "quantking_legacy_qdb",
}
