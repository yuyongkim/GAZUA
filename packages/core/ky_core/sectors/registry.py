"""Sector indicator registry — concrete parameters for the WICS-34 map.

Each entry pins a specific (sector, indicator_name, source, **params) tuple
so ``scripts/collect_sectors.py`` can iterate the matrix and pull data
without per-call configuration.

Registry rows are intentionally redundant when the same indicator (e.g. KIS
업종지수) repeats across sectors — that mirrors how the user actually scans
sectors in parallel and keeps the collector code uniform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IndicatorSpec:
    sector: str            # WICS sector key, e.g. "semiconductor"
    sector_label: str      # human label
    name: str              # short indicator name
    source: str            # adapter id (matches CustomsAdapter.source_id etc.)
    method: str            # adapter method name to call
    params: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def slug(self) -> str:
        clean = self.name.replace(" ", "_").replace("/", "_")
        return f"{self.sector}__{clean}"


# ---------------------------------------------------------------------------
# Customs HS codes — Korean export-driven sectors.
# Format: (sector, sector_label, name, hs_code).
# Single hs_code per row; multi-HS sectors register multiple rows.
# ---------------------------------------------------------------------------

CUSTOMS_HS = [
    ("semiconductor", "반도체",     "메모리·반도체 수출 (HS 8542)",            "8542"),
    ("display",       "디스플레이",  "디스플레이 모듈 수출 (HS 8528)",          "8528"),
    ("handset",       "핸드셋",     "휴대폰 수출 (HS 8517)",                "8517"),
    ("auto",          "자동차",     "자동차 부품 수출 (HS 8708)",             "8708"),
    ("auto",          "자동차",     "자동차 완성차 수출 (HS 8703)",            "8703"),
    ("chemical",      "화학",      "기초 폴리머 수출 (HS 3901)",              "3901"),
    ("chemical",      "화학",      "PE/PP 수출 (HS 3902)",                  "3902"),
    ("chemical",      "화학",      "기타 폴리머 수출 (HS 3907)",              "3907"),
    ("chemical",      "화학",      "플라스틱 제품 수출 (HS 3923)",            "3923"),
    ("steel",         "철강",      "철강제품 수출 (HS 7208-7229 대표 HS72)",  "72"),
    ("nonferrous",    "비철금속",   "구리 (HS 74)",                          "74"),
    ("nonferrous",    "비철금속",   "알루미늄 (HS 76)",                      "76"),
    ("nonferrous",    "비철금속",   "아연 (HS 79)",                          "79"),
    ("ship",          "조선",      "선박 부품 (HS 8906)",                   "8906"),
    ("ship",          "조선",      "선박 완성품 (HS 8901)",                  "8901"),
    ("machinery",     "기계",      "일반기계 (HS 84)",                      "84"),
    ("electric",      "전기장비",   "전기장비 (HS 85 — 8542 제외 추정)",       "85"),
    ("itappliance",   "IT가전",    "냉장고/세탁기 (HS 8418/8450 대표 HS8418)", "8418"),
    ("itappliance",   "IT가전",    "기타 가전 (HS 8516)",                    "8516"),
    ("electronics",   "전기·전자", "전기·전자제품 종합 (HS 85)",              "85"),
    ("paper",         "종이·목재",  "펄프 (HS 47)",                          "47"),
    ("paper",         "종이·목재",  "종이 (HS 48)",                          "48"),
    ("food",          "식품",      "농수산식품 (HS 16-22 대표 HS16)",         "16"),
    ("pharma",        "제약",      "의약품 (HS 30)",                         "30"),
    ("cosmetic",      "화장품",    "화장품 (HS 3304)",                       "3304"),
    ("energy",        "에너지",    "석유제품 (HS 2710)",                     "2710"),
    ("energy",        "에너지",    "원유 수입 (HS 2709)",                    "2709"),
]


def _customs_specs() -> list[IndicatorSpec]:
    """data.go.kr 관세청 API enforces a max 12-month window per call. The
    adapter's ``get_hs_country_history`` chunks the range internally so we
    can request multi-year (1995~) windows from the registry directly."""
    out: list[IndicatorSpec] = []
    for sector, label, name, hs in CUSTOMS_HS:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label=label,
                name=name,
                source="customs",
                method="get_hs_country_history",
                params={
                    "hs_code": hs,
                    "year_month_start": "199501",
                    "year_month_end": "202604",
                    "num_rows": 1000,
                },
                note=f"HS {hs}",
            )
        )
    # 10-day provisional (verified 2026-04-26) — country & item dimensions.
    # priodYear / priodMon / priodDt + itemUsdAmt00..10 (00=총합, 01-10=TOP 10).
    for dim, dim_label, flow, method in (
        ("country", "국가별", "export", "get_10day_export_by_country"),
        ("country", "국가별", "import", "get_10day_import_by_country"),
        ("item",    "주요품목", "export", "get_10day_export_by_item"),
        ("item",    "주요품목", "import", "get_10day_import_by_import_by_item" if False else "get_10day_import_by_item"),
    ):
        out.append(
            IndicatorSpec(
                sector="trade",
                sector_label="무역",
                name=f"{dim_label} 10일 잠정 {flow.upper()}",
                source="customs",
                method=method,
                params={
                    "year_month_start": "202604",
                    "year_month_end":   "202604",
                    "num_rows": 200,
                },
                note=f"{flow} 10-day {dim} provisional",
            )
        )
    # 국가별 월 누적 — 주요 5개 무역국 (US/CN/JP/VN/DE) 1995~ 다년치
    for cnty, name in [
        ("US", "미국"), ("CN", "중국"), ("JP", "일본"),
        ("VN", "베트남"), ("DE", "독일"),
    ]:
        out.append(
            IndicatorSpec(
                sector="trade",
                sector_label="무역",
                name=f"국가별 월 — {name}({cnty})",
                source="customs",
                method="get_country_history",
                params={
                    "country_code": cnty,
                    "year_month_start": "199501",
                    "year_month_end":   "202604",
                    "num_rows": 1000,
                },
                note=f"country={cnty}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# FRED — global macro that recurs across many sectors. Pull each series once;
# multiple sectors can read from the same dump.
# ---------------------------------------------------------------------------

FRED_SERIES = [
    ("macro", "FED 기준금리",         "FEDFUNDS"),
    ("macro", "10Y Treasury",       "DGS10"),
    ("macro", "2Y Treasury",        "DGS2"),
    ("macro", "10Y-2Y 스프레드",      "T10Y2Y"),
    ("macro", "10Y-3M 스프레드",      "T10Y3M"),
    ("macro", "30Y Mortgage Rate",  "MORTGAGE30US"),
    ("macro", "Dollar Index (DXY)", "DTWEXBGS"),
    ("macro", "VIX 변동성지수",       "VIXCLS"),
    ("macro", "Industrial Production", "INDPRO"),
    ("macro", "Capacity Utilization",  "TCU"),
    ("auto",   "미국 신차 SAAR (TOTALSA)", "TOTALSA"),
    ("retail", "미국 소매판매",          "RSAFS"),
    ("retail", "미국 소비자신뢰지수",      "UMCSENT"),
    ("construction", "미국 신규주택착공", "HOUST"),
    ("oil",    "WTI 원유",              "DCOILWTICO"),
    ("oil",    "Brent 원유",            "DCOILBRENTEU"),
    ("macro",  "미국 ISM 제조업 신규수주", "NEWORDER"),
    ("macro",  "PCE Inflation",          "PCEPI"),
    ("macro",  "Core CPI",              "CPILFESL"),
]


def _fred_specs() -> list[IndicatorSpec]:
    out: list[IndicatorSpec] = []
    for sector, name, sid in FRED_SERIES:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label="매크로",
                name=name,
                source="fred",
                method="get_series",
                params={"series_id": sid, "start": "1990-01-01"},
                note=sid,
            )
        )
    return out


# ---------------------------------------------------------------------------
# OECD CLI — major economies. The CLI dataflow is ``DSD_STES@DF_CLI``.
# ---------------------------------------------------------------------------

OECD_CLI_COUNTRIES = ["KOR", "USA", "CHN", "JPN", "DEU"]


def _oecd_specs() -> list[IndicatorSpec]:
    return [
        IndicatorSpec(
            sector="macro",
            sector_label="매크로",
            name=f"OECD CLI {country}",
            source="oecd",
            method="get_cli",
            params={"country_iso": country, "recent": 480},
            note=f"Composite Leading Indicator — {country}",
        )
        for country in OECD_CLI_COUNTRIES
    ]


# ---------------------------------------------------------------------------
# World Bank — KOR + major trading partners; long-run structural metrics.
# ---------------------------------------------------------------------------

WORLDBANK_INDICATORS = [
    ("KOR", "NY.GDP.MKTP.CD",    "한국 명목 GDP (USD)"),
    ("KOR", "NV.IND.MANF.ZS",    "한국 제조업 GDP 비중 (%)"),
    ("CHN", "NY.GDP.MKTP.KD.ZG", "중국 GDP 성장률"),
    ("USA", "NY.GDP.MKTP.KD.ZG", "미국 GDP 성장률"),
    ("WLD", "NY.GDP.MKTP.KD.ZG", "글로벌 GDP 성장률"),
    ("IND", "NY.GDP.MKTP.KD.ZG", "인도 GDP 성장률"),
    ("IND", "NY.GDP.MKTP.CD",    "인도 명목 GDP"),
    ("KOR", "SP.POP.TOTL",       "한국 인구"),
    ("KOR", "SP.POP.65UP.TO.ZS", "한국 65세 이상 비중"),
    # NATO 회원국 국방비 (대표국 5개)
    ("USA", "MS.MIL.XPND.GD.ZS", "미국 GDP 대비 국방비"),
    ("DEU", "MS.MIL.XPND.GD.ZS", "독일 GDP 대비 국방비"),
    ("FRA", "MS.MIL.XPND.GD.ZS", "프랑스 GDP 대비 국방비"),
    ("GBR", "MS.MIL.XPND.GD.ZS", "영국 GDP 대비 국방비"),
    ("KOR", "MS.MIL.XPND.GD.ZS", "한국 GDP 대비 국방비"),
]


def _worldbank_specs() -> list[IndicatorSpec]:
    return [
        IndicatorSpec(
            sector="macro",
            sector_label="매크로",
            name=name,
            source="worldbank",
            method="get_indicator",
            params={"country_iso": iso, "indicator": ind, "date_range": "1990:2025"},
            note=ind,
        )
        for iso, ind, name in WORLDBANK_INDICATORS
    ]


# ---------------------------------------------------------------------------
# pytrends — Korean sentiment keywords aligned to WICS sectors.
# ---------------------------------------------------------------------------

PYTRENDS_KEYWORDS = [
    ("semiconductor", "GPU"),
    ("semiconductor", "HBM"),
    ("semiconductor", "메모리 반도체"),
    ("display",       "OLED"),
    ("handset",       "iPhone"),
    ("handset",       "Galaxy"),
    ("auto",          "전기차"),
    ("auto",          "EV"),
    ("retail",        "쿠팡"),
    ("media",         "K-pop"),
    ("media",         "K-drama"),
    ("cosmetic",      "K-beauty"),
    ("food",          "라면"),
    ("pharma",        "GLP-1"),
    ("ship",          "조선"),
    ("defense",       "방산"),
]


def _pytrends_specs() -> list[IndicatorSpec]:
    """pytrends — Google Trends. timeframe='all' = 2004~ (Google Trends 시작 시점).
    이 이상 깊이는 Google이 제공 안 함."""
    return [
        IndicatorSpec(
            sector=sector,
            sector_label=sector,
            name=f"검색관심도 '{kw}'",
            source="pytrends",
            method="interest_over_time",
            params={"keywords": [kw], "geo": "KR", "timeframe": "all"},
            note=kw,
        )
        for sector, kw in PYTRENDS_KEYWORDS
    ]


# ---------------------------------------------------------------------------
# CFTC COT — commodity / FX positioning.
# ---------------------------------------------------------------------------

CFTC_MARKETS = [
    "CRUDE OIL",
    "GOLD",
    "COPPER",
    "JAPANESE YEN",
    "EURO FX",
]


def _cftc_specs() -> list[IndicatorSpec]:
    """CFTC COT — 30년 백필. limit=2000 weeks ≈ 38년."""
    return [
        IndicatorSpec(
            sector="macro",
            sector_label="매크로",
            name=f"COT — {m}",
            source="cftc",
            method="search_market",
            params={"market_keyword": m, "limit": 2000},
            note=m,
        )
        for m in CFTC_MARKETS
    ]


# ---------------------------------------------------------------------------
# ECOS (한국은행) — Korean rates, credit, sentiment, real estate.
# stat_code · item_code 조합은 한은 ECOS 통계검색에서 확정한 값 사용.
# (start/end YYYYMMDD for daily, YYYYMM for monthly, YYYY for annual.)
# ---------------------------------------------------------------------------

ECOS_SERIES = [
    # 30년 백필: 시리즈마다 데이터 가용 시작점이 달라 일괄 1990/1995로 두고
    # ECOS가 가용한 시점부터 반환하도록 위임. 종료는 collect 시점 기준 미래로.
    # 금리 — 단·중·장기 + 회사채 신용스프레드
    ("macro",       "한국 기준금리",            "722Y001", "0101000", "M", "199001", "203012"),
    ("macro",       "콜금리(1일물)",            "722Y001", "0101100", "D", "19900101", "20301231"),
    ("macro",       "국고채 3Y",               "817Y002", "010200000", "D", "19900101", "20301231"),
    ("macro",       "국고채 10Y",              "817Y002", "010210000", "D", "19900101", "20301231"),
    ("macro",       "회사채 AA- 3Y",          "817Y002", "010320000", "D", "19900101", "20301231"),
    ("macro",       "회사채 BBB- 3Y",         "817Y002", "010330000", "D", "19900101", "20301231"),
    # 가계 / 부동산
    ("bank",        "가계대출 잔액",            "151Y005", "1000000",   "M", "199001", "203012"),
    ("realestate",  "주택매매가격지수 (전국)",   "901Y063", "S22A",      "M", "199001", "203012"),
    # 환율
    ("macro",       "원/달러 환율 (종가)",      "731Y001", "0000001",   "D", "19900101", "20301231"),
    ("macro",       "원/엔(100엔) 환율",        "731Y001", "0000002",   "D", "19900101", "20301231"),
    # 심리지표
    ("macro",       "BSI 제조업 업황",          "512Y014", "AX1AA",     "M", "199001", "203012"),
    ("macro",       "CSI 종합소비자심리지수",    "511Y002", "FME",       "M", "199001", "203012"),
]


def _ecos_specs() -> list[IndicatorSpec]:
    out: list[IndicatorSpec] = []
    for sector, name, stat, item, freq, start, end in ECOS_SERIES:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label="매크로" if sector == "macro" else sector,
                name=name,
                source="ecos",
                method="get_series",
                params={
                    "stat_code": stat,
                    "item_code": item,
                    "start": start,
                    "end": end,
                    "freq": freq,
                },
                note=f"{stat}/{item}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# KOSIS (통계청) — 산업별 생산·출하·재고 지수.
# org_id=101 (통계청). tblId codes from the 광공업동향조사 family.
# Some entries leave item_code/obj_l1 None and let last_n_periods drive the
# window — KOSIS auto-returns the canonical default.
# ---------------------------------------------------------------------------

KOSIS_SERIES = [
    # ── 단일 호출로 가능한 시리즈 (40K 셀 이하) ──────────────────
    # 한국은행 (orgId=301)
    ("macro", "GDP by 경제활동 (분기)", "301", "DT_200Y105", "ALL", "ALL", "Q"),
    # 통계청 (orgId=101) — 경제활동인구조사 (1999~)
    ("macro", "경제활동인구",        "101", "DT_1DA7002S", "T20", "00", "M"),
    ("macro", "취업자수",           "101", "DT_1DA7002S", "T30", "00", "M"),
    ("macro", "실업자수",           "101", "DT_1DA7002S", "T40", "00", "M"),
    ("macro", "경제활동참가율",      "101", "DT_1DA7002S", "T60", "00", "M"),
    ("macro", "고용률",             "101", "DT_1DA7002S", "T90", "00", "M"),
    # 통계청 — 광공업 동향조사 (작은 테이블)
    ("macro", "제조업 재고율",                "101", "DT_1F02013", "ALL", "ALL", "M"),
    ("macro", "제조업 평균가동률",            "101", "DT_1F32002", "ALL", "ALL", "M"),
    ("macro", "업종별 국내공급지수",          "101", "DT_1MS2001", "ALL", "ALL", "M"),
]

# ── 큰 테이블 (40K 셀 초과 — 청킹 필수) ───────────────────────
# get_data_chunked로 호출. start_prd/end_prd로 범위 지정.
# parent='L' → L_4 (광업제조업동향조사) → 101_G131/G132 트리에서 발굴.
KOSIS_SERIES_CHUNKED = [
    # (sector, name, org_id, tbl_id, item_code, obj_l1, prd_se, start, end, chunk_years)
    ("macro", "품목별 광공업 생산출하재고",
     "101", "DT_1F02012", "ALL", "ALL", "M", "199001", "202612", 2),
    ("macro", "내수수출 광공업출하지수",
     "101", "DT_1F02016", "ALL", "ALL", "M", "199001", "202612", 2),
    ("macro", "제조업 생산능력 및 가동률지수",
     "101", "DT_1F32001", "ALL", "ALL", "M", "199001", "202612", 2),
    # 다차원 테이블 (시도/산업별 등) DT_1F02001/03/04/05/07/11/31 + DT_1MS2003-2009 는
    # objL1+L2+L3 명시 필요 — 추후 KOSIS 포털 lookup 후 별도 트랙으로 추가.
    # ※ 무역 시계열은 관세청 customs 어댑터가 1995-01~ HS-level 30년치를
    #   커버하므로 KOSIS 산업별 수출입 보강은 우선순위 낮음 (KOSIS 자체에 거의 없음).
]


def _kosis_specs() -> list[IndicatorSpec]:
    """KOSIS 시계열 — 30년 백필.

    - 작은 테이블: ``get_data`` + ``last_n_periods`` (Q 480 / M 600 / D 10000 / Y 60)
    - 큰 테이블 (40K 셀 초과): ``get_data_chunked`` (KOSIS_SERIES_CHUNKED)
    """
    period_count = {"Q": 480, "M": 600, "D": 10000, "Y": 60}
    out: list[IndicatorSpec] = []
    for sector, name, org_id, tbl_id, item_code, obj_l1, prd_se in KOSIS_SERIES:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label=sector,
                name=name,
                source="kosis",
                method="get_data",
                params={
                    "org_id": org_id,
                    "tbl_id": tbl_id,
                    "item_code": item_code,
                    "obj_l1": obj_l1,
                    "prd_se": prd_se,
                    "last_n_periods": period_count.get(prd_se, 600),
                },
                note=f"{org_id}/{tbl_id}/{item_code}/{obj_l1}",
            )
        )
    for sector, name, org_id, tbl_id, item_code, obj_l1, prd_se, start, end, chunk_y in KOSIS_SERIES_CHUNKED:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label=sector,
                name=name,
                source="kosis",
                method="get_data_chunked",
                params={
                    "org_id": org_id,
                    "tbl_id": tbl_id,
                    "item_code": item_code,
                    "obj_l1": obj_l1,
                    "prd_se": prd_se,
                    "start_prd": start,
                    "end_prd": end,
                    "chunk_years": chunk_y,
                },
                note=f"{org_id}/{tbl_id} chunked {start}~{end} ({chunk_y}y)",
            )
        )
    return out


# ---------------------------------------------------------------------------
# EIA — US energy series referenced across 화학·에너지·자동차·운송·유틸리티.
# v2 endpoints use path + series_code. We use both routes:
#   - get_series(series_id) for the "seriesid" legacy compat ones
#   - get_path_series(path, series_code) for v2 weekly/monthly paths
# ---------------------------------------------------------------------------

EIA_SERIES = [
    # (sector, name, method, args)
    # 30년 length로 확장 (daily 9000 ≈ 35년, weekly 2000 ≈ 38년)
    ("energy",   "WTI 원유 (현물)",    "get_path_series",
        {"path": "petroleum/pri/spt", "series_code": "RWTC", "frequency": "daily", "length": 9000}),
    ("energy",   "Brent 원유 (현물)",  "get_path_series",
        {"path": "petroleum/pri/spt", "series_code": "RBRTE", "frequency": "daily", "length": 9000}),
    ("energy",   "미국 원유 재고 (Crude Stocks)", "get_path_series",
        {"path": "petroleum/stoc/wstk", "series_code": "WCESTUS1", "frequency": "weekly", "length": 2000}),
    ("energy",   "미국 정제시설 가동률", "get_path_series",
        {"path": "petroleum/pnp/wiup", "series_code": "WPULEUS3", "frequency": "weekly", "length": 2000}),
    # 2026-04-26 series_code 수정: PE1/PE_ → PF4_ (EIA v2 새 코딩 체계)
    ("chemical", "Heating Oil (#2 NY)", "get_path_series",
        {"path": "petroleum/pri/spt", "series_code": "EER_EPD2F_PF4_Y35NY_DPG", "frequency": "weekly", "length": 2000}),
    # Henry Hub: path은 natural-gas/pri/sum이 아니라 natural-gas/pri/fut
    ("oil",      "천연가스 (Henry Hub) 일별", "get_path_series",
        {"path": "natural-gas/pri/fut", "series_code": "RNGWHHD", "frequency": "daily", "length": 9000}),
    # 정제가동률 weekly만 지원 (monthly가 invalid frequency)
    ("oil",      "정제가동률 가중평균 (weekly)", "get_path_series",
        {"path": "petroleum/pnp/wiup", "series_code": "WPULEUS3", "frequency": "weekly", "length": 2000}),
    ("auto",     "미국 휘발유 소매가",    "get_path_series",
        {"path": "petroleum/pri/gnd", "series_code": "EMM_EPMR_PTE_NUS_DPG", "frequency": "weekly", "length": 2000}),
    ("transport","미국 디젤 도매가",     "get_path_series",
        {"path": "petroleum/pri/spt", "series_code": "EER_EPD2DXL0_PF4_RGC_DPG", "frequency": "weekly", "length": 2000}),
]


def _eia_specs() -> list[IndicatorSpec]:
    out: list[IndicatorSpec] = []
    for sector, name, method, params in EIA_SERIES:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label=sector,
                name=name,
                source="eia",
                method=method,
                params=params,
                note=params.get("series_code", ""),
            )
        )
    return out


# ---------------------------------------------------------------------------
# DART — quarterly / annual financials for the major sector flagship.
# corp_code values from DART CORPCODE.xml (publicly available).
# ---------------------------------------------------------------------------

DART_FLAGSHIP = [
    # (sector, sector_label, corp_code, name) — verified against DART CORPCODE.xml 2026-04-26
    # 모든 corp_code는 corpCode.xml lookup으로 검증됨 (lookup script: scripts/lookup_corps.py)
    # 반도체
    ("semiconductor", "반도체", "00126380", "삼성전자"),
    ("semiconductor", "반도체", "00164779", "SK하이닉스"),
    # 디스플레이
    ("display",       "디스플레이", "00105873", "LG디스플레이"),
    # 화학
    ("chemical",      "화학",   "00356361", "LG화학"),         # FIX: prev 00126186 (=삼성SDS)
    ("chemical",      "화학",   "00631518", "SK이노베이션"),
    ("chemical",      "화학",   "00165413", "롯데케미칼"),
    # 자동차
    ("auto",          "자동차", "00164742", "현대자동차"),
    ("auto",          "자동차", "00106641", "기아"),
    ("auto",          "자동차", "00164788", "현대모비스"),
    # 조선
    ("ship",          "조선",   "01390344", "HD현대중공업"),
    ("ship",          "조선",   "00126478", "삼성중공업"),
    ("ship",          "조선",   "00111704", "한화오션"),
    # 철강
    ("steel",         "철강",   "00155319", "POSCO홀딩스"),    # FIX: prev 00126308 (=호텔신라/한전 아님; 정확코드)
    ("steel",         "철강",   "00145880", "현대제철"),
    # 비철금속
    ("nonferrous",    "비철금속", "00102858", "고려아연"),
    ("nonferrous",    "비철금속", "00684714", "풍산"),
    # 은행
    ("bank",          "은행",   "00688996", "KB금융"),
    ("bank",          "은행",   "00382199", "신한지주"),
    ("bank",          "은행",   "00547583", "하나금융지주"),
    # 증권
    ("broker",        "증권",   "00311030", "미래에셋증권"),
    ("broker",        "증권",   "00432102", "한국금융지주"),
    ("broker",        "증권",   "00104856", "삼성증권"),
    # 보험
    ("insurance",     "보험",   "00126256", "삼성생명"),
    ("insurance",     "보험",   "00139214", "삼성화재"),
    ("insurance",     "보험",   "00159102", "DB손해보험"),
    # 다각화 금융 (캐피탈·카드)
    ("fincap",        "금융",   "00126292", "삼성카드"),
    # 유통
    ("retail",        "유통",   "00872984", "이마트"),
    ("retail",        "유통",   "00120526", "롯데쇼핑"),
    # 호텔·레저
    ("tourism",       "호텔레저", "00165680", "호텔신라"),
    ("tourism",       "호텔레저", "00255619", "강원랜드"),
    ("tourism",       "호텔레저", "00171265", "파라다이스"),
    # 미디어
    ("media",         "엔터",   "00258689", "JYP Ent."),
    ("media",         "엔터",   "01204056", "HYBE"),
    ("media",         "엔터",   "00260930", "에스엠"),
    ("media",         "엔터",   "00265324", "CJ ENM"),
    # 화장품
    ("cosmetic",      "화장품", "00356370", "LG생활건강"),
    ("cosmetic",      "화장품", "00583424", "아모레퍼시픽"),
    # 식품
    ("food",          "식품",   "00635134", "CJ제일제당"),
    ("food",          "식품",   "00108241", "농심"),
    ("food",          "식품",   "00141529", "오뚜기"),
    ("food",          "담배",   "00244455", "KT&G"),
    # 제약·바이오
    ("pharma",        "제약",   "00413046", "셀트리온"),
    ("pharma",        "제약",   "00877059", "삼성바이오로직스"),
    ("pharma",        "제약",   "00828497", "한미약품"),
    ("pharma",        "제약",   "00145109", "유한양행"),
    # 통신
    ("telecom",       "통신",   "00159023", "SK텔레콤"),
    ("telecom",       "통신",   "00190321", "KT"),
    ("telecom",       "통신",   "00231363", "LG유플러스"),
    # 유틸리티
    ("utility",       "유틸리티", "00159193", "한국전력공사"),
    ("utility",       "유틸리티", "00261285", "한국가스공사"),
    # IT 서비스 / 소프트웨어
    ("software",      "소프트웨어", "00258801", "카카오"),
    ("software",      "소프트웨어", "00266961", "NAVER"),
    ("itservice",     "IT서비스", "00126186", "삼성에스디에스"),  # 00126186은 삼성SDS
    # 일반전기전자
    ("electronics",   "전기전자", "00126371", "삼성전기"),
    ("electronics",   "전기전자", "00105961", "LG이노텍"),
    # 방산
    ("defense",       "방산",   "00126566", "한화에어로스페이스"),
    ("defense",       "방산",   "00309503", "한국항공우주"),
    ("defense",       "방산",   "00302926", "현대로템"),
    # 2026-04-26 cell-level verification 후 추가 (5대 건설사·운송·기계·종이)
    # 5대 건설사
    ("construction",  "건설",   "00164478", "현대건설"),
    ("construction",  "건설",   "00120030", "GS건설"),
    ("construction",  "건설",   "00124540", "대우건설"),
    ("construction",  "건설",   "00149655", "삼성물산"),  # 28260 (지주)
    ("construction",  "건설",   "01524093", "DL이앤씨"),
    # 기계
    ("machinery",     "기계",   "01032486", "두산밥캣"),
    # 운송 (해운/항공)
    ("transport",     "운송",   "00164645", "HMM"),
    ("transport",     "운송",   "00122737", "팬오션"),
    ("transport",     "운송",   "00113526", "대한항공"),
    # 종이·목재
    ("paper",         "종이",   "00136086", "무림페이퍼"),
    ("paper",         "종이",   "00119007", "무림P&P"),
    ("paper",         "종이",   "01060744", "한솔제지"),
    ("paper",         "종이",   "00138729", "아세아제지"),
]

DART_REPORT_YEARS = list(range(2000, 2026))  # 2000~2025 — DART 시작년도부터 (annual report only)


def _dart_specs() -> list[IndicatorSpec]:
    out: list[IndicatorSpec] = []
    for sector, label, corp, name in DART_FLAGSHIP:
        for year in DART_REPORT_YEARS:
            out.append(
                IndicatorSpec(
                    sector=sector,
                    sector_label=label,
                    name=f"{name} 사업보고서 {year}",
                    source="dart",
                    method="get_financial_statements",
                    params={
                        "corp_code": corp,
                        "year": year,
                        "report_code": "11011",  # annual
                        "fs_div": "CFS",
                    },
                    note=f"{name}/{year}",
                )
            )
    return out


# ---------------------------------------------------------------------------
# KIS — KOSPI 업종지수 일별 OHLCV (12개월).
# FID_INPUT_ISCD codes from KRX 업종 분류. 매핑은 KOSPI 종합 + 11개 핵심
# 업종 + 코스닥 종합. WICS 34와 1:1은 아니지만 섹터 모멘텀 가격축에 충분.
# ---------------------------------------------------------------------------

KIS_INDEX_CODES = [
    # Verified-returning-50-rows codes (KIS returns max 50 daily rows per call,
    # ~10 weeks). Pagination + multi-call needed for longer history.
    ("market",        "KOSPI 종합",       "0001"),
    ("market",        "KOSDAQ 종합",      "1001"),
    ("market",        "KOSPI 200",        "2001"),
    ("food",          "KOSPI 음식료품",    "0010"),
    ("paper",         "KOSPI 섬유의복",    "0020"),
    ("paper",         "KOSPI 종이목재",    "0030"),
    # Codes 0040+ return 0 rows on the FHKUP03500100 endpoint; KIS uses a
    # different code system for finer 업종 indices that needs lookup.
    # Tracked as future work in CLAUDE.md.
]


def _kis_index_specs() -> list[IndicatorSpec]:
    """KIS 업종지수 — adapter의 get_index_daily_history가 50일 한계를
    페이지네이션으로 우회. 1995-01부터 현재까지 일봉 OHLCV 백필."""
    out: list[IndicatorSpec] = []
    for sector, name, code in KIS_INDEX_CODES:
        out.append(
            IndicatorSpec(
                sector=sector,
                sector_label=sector,
                name=name,
                source="kis_index",
                method="get_index_daily_history",
                params={
                    "sector_code": code,
                    "date_from": "19950101",
                    "date_to":   "20260425",
                    "period": "D",
                },
                note=f"KRX {code}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Crawler-backed series (yfinance / openFDA / stooq) — formerly the "Crawl⚠"
# bucket in the WICS-34 map. Stability is lower than institutional APIs.
# ---------------------------------------------------------------------------

YF_COMMODITIES = [
    # Direct hits on previously Crawl-only WICS-34 cells (verified 2026-04-26):
    ("nonferrous",  "COMEX 구리 (HG=F)",         "HG=F"),    # [03] 비철 #1
    ("nonferrous",  "CME 알루미늄 (ALI=F)",       "ALI=F"),   # [03] 비철 #2
    ("nonferrous",  "Zinc futures (ZN=F)",      "ZN=F"),    # [03] 비철 #3
    ("steel",       "Iron Ore futures (TIO=F)", "TIO=F"),   # [04] 철강 #1 중국 철광석 proxy
    ("ship",        "BDI ETF (BDRY)",           "BDRY"),    # [07] 조선 #2 / [09] 운송 #2
    ("transport",   "Shipping ETF (SEA)",       "SEA"),     # [09] 운송 일반
    ("auto",        "Lithium ETF (LIT)",        "LIT"),     # [10] 자동차 #6 리튬
    ("oil",         "WTI 원유 (CL=F)",            "CL=F"),
    ("oil",         "Natural Gas ETF (BOIL)",   "BOIL"),    # [30] 유틸리티 보조
    ("macro",       "은 (SI=F)",                  "SI=F"),
    ("macro",       "금 (GC=F)",                  "GC=F"),
    ("macro",       "Palladium (PALL)",          "PALL"),
    # ── 한국 sector ETFs (KIS 업종지수 0040+ 미커버 보강) — 2026-04-26 추가 ──
    # KODEX/TIGER 시리즈는 yfinance에서 .KS suffix로 일별 가격 조회 가능.
    # 30년 깊이는 ETF 상장년부터 (대부분 2002~2010년 시작).
    ("retail",      "KODEX 유통 (091170.KS)",      "091170.KS"),  # [14] 유통
    ("food",        "KODEX 음식료 (140710.KS)",    "140710.KS"),  # [15][16] 식품
    ("pharma",      "KODEX 헬스케어 (266420.KS)",   "266420.KS"),  # [17] 제약
    ("bank",        "KODEX 은행 (091170.KS)",       "091170.KS"),  # [18] 은행 (overlap with retail)
    ("broker",      "KODEX 증권 (102970.KS)",       "102970.KS"),  # [19] 증권
    ("insurance",   "KODEX 보험 (140700.KS)",       "140700.KS"),  # [20] 보험
    ("software",    "TIGER 200 IT (139260.KS)",   "139260.KS"),  # [22] 소프트웨어
    ("itservice",   "KODEX IT (266370.KS)",       "266370.KS"),  # [23] IT 서비스
    ("display",     "TIGER 디스플레이 (139220.KS)", "139220.KS"),  # [24] 디스플레이
    ("semiconductor","KODEX 반도체 (091160.KS)",   "091160.KS"),  # [26] 반도체
    ("electric",    "KODEX 산업재 (140710.KS)",     "140710.KS"),  # [28] 전기장비
    ("telecom",     "TIGER 200 통신 (139310.KS)",  "139310.KS"),  # [29] 통신
    ("utility",     "KODEX 인버스 (114800.KS)",     "114800.KS"),  # placeholder; 한국 utility ETF 없음
    ("realestate",  "TIGER 부동산 (157500.KS)",    "157500.KS"),  # [31] 부동산 REITs
    ("defense",     "ARIRANG 우주항공 (449290.KS)", "449290.KS"),  # [32] 방산 (신규 ETF 2023)
    ("auto",        "KODEX 자동차 (091180.KS)",     "091180.KS"),  # [10] 자동차
    ("steel",       "KODEX 철강 (117680.KS)",       "117680.KS"),  # [04] 철강
    ("chemical",    "KODEX 화학 (091230.KS)",       "091230.KS"),  # [02] 화학
    ("ship",        "KODEX 조선 (102960.KS)",       "102960.KS"),  # [07] 조선
    ("media",       "TIGER K-게임 (300610.KS)",     "300610.KS"),  # [13] 미디어/게임
    ("cosmetic",    "TIGER 화장품 (228790.KS)",    "228790.KS"),  # [11] 화장품
    ("construction","KODEX 건설 (117700.KS)",      "117700.KS"),  # [05] 건설
    ("energy",      "KODEX 에너지화학 (117460.KS)", "117460.KS"),  # [01] 에너지
    ("nonferrous",  "TIGER 비철금속 (139250.KS)",  "139250.KS"),  # [03] 비철금속 추가
]


def _yfinance_specs() -> list[IndicatorSpec]:
    return [
        IndicatorSpec(
            sector=sec, sector_label=sec, name=name,
            source="yf_commodities", method="get_history",
            params={"symbol": sym, "period": "max"},
            note=sym,
        )
        for sec, name, sym in YF_COMMODITIES
    ]


def _openfda_specs() -> list[IndicatorSpec]:
    return [
        IndicatorSpec(
            sector="pharma", sector_label="제약·바이오",
            name="FDA 의약품 승인 건수 (월별)",
            source="openfda", method="get_drug_approvals_monthly",
            params={"start_year": 2014},
            note="FDA drug approvals monthly",
        ),
    ]


def _stooq_specs() -> list[IndicatorSpec]:
    # Stooq blocks heavily — keep this minimal. Sleep is enforced by
    # collect_sectors.py per-source delay. We probe a few extra symbols just
    # to fill what yfinance can't (al.c cash vs futures, iron ore proxies).
    return [
        IndicatorSpec(
            sector="nonferrous", sector_label="비철금속",
            name="Stooq 알루미늄 (al.c)",
            source="stooq", method="get_quote",
            params={"symbol": "al.c"},
            note="aluminum cash (Stooq)",
        ),
    ]


# ---------------------------------------------------------------------------
# UN Comtrade — global view of Korea's HS exports for cross-validation.
# Limited to a handful since the public preview tier is lag-y and rate-limited.
# ---------------------------------------------------------------------------

COMTRADE_HS = ["8542", "8703", "3901", "8901"]


def _comtrade_specs() -> list[IndicatorSpec]:
    """UN Comtrade — 30년 백필. 연간(freq='A') 1995~2024, comma-joined period."""
    return [
        IndicatorSpec(
            sector="trade",
            sector_label="무역",
            name=f"Korea→World HS {hs} (annual)",
            source="un_comtrade",
            method="get_trade_history",
            params={
                "reporter_iso3": "KOR",
                "partner_iso3": "WLD",
                "hs_code": hs,
                "flow": "X",
                "year_start": 1995,
                "year_end": 2024,
            },
            note=hs,
        )
        for hs in COMTRADE_HS
    ]


# ---------------------------------------------------------------------------
# Aggregate — public API
# ---------------------------------------------------------------------------

def all_specs() -> list[IndicatorSpec]:
    """Return only specs that hit external APIs.

    Note: 2026-04-26 30년 백필 정책 변경 — 기존엔 ECOS는 Economic_analysis
    임포트(Nov 2025 스냅샷)로 대체했으나, 사장님 지시로 라이브 ECOS API를
    1990~ 일괄 백필하도록 ``_ecos_specs``를 다시 포함시킴. 임포트본은
    ``imported/ecos/`` 별도 디렉토리에 그대로 남아 있어 충돌 없음.
    KOSIS 산업생산 시리즈는 tblId 발굴 후 별도 트랙으로 추가.
    """
    return [
        *_customs_specs(),
        *_fred_specs(),       # 19 macro series, 1990-01-01 ~ now (~30년)
        *_ecos_specs(),       # 12 한국 매크로 (라이브 API, 1990~)
        *_kosis_specs(),      # GDP by industry (only one verified) + 산업생산 (TODO)
        *_eia_specs(),        # 10 US energy series
        *_dart_specs(),       # 18 sector flagships × multi-year reports
        *_kis_index_specs(),  # 23 KOSPI/KOSDAQ sector indices (페이지네이션 지원시 ~5년)
        *_yfinance_specs(),   # 13 global commodity histories (period=max ~30년)
        *_openfda_specs(),    # FDA drug approvals monthly
        *_stooq_specs(),      # 1 stooq fallback (aluminum cash)
        *_oecd_specs(),
        *_worldbank_specs(),
        *_pytrends_specs(),
        *_cftc_specs(),
        *_comtrade_specs(),
    ]


def specs_by_source(source: str) -> list[IndicatorSpec]:
    return [s for s in all_specs() if s.source == source]


def total_count() -> int:
    return len(all_specs())
