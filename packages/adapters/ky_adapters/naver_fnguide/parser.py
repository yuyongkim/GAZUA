"""Parsers for the Naver / FnGuide snapshot payloads.

Five dialects are supported:

* ``m.stock.naver.com/api/stock/{symbol}/integration`` — JSON. Compact but
  stable; we prefer it. It exposes summary quotes, consensus, peers, major
  shareholders and the most recent financial highlights.
* ``m.stock.naver.com/api/stock/{symbol}/finance/{annual|quarter}`` — JSON.
  ``financeInfo.rowList`` exposes 16 headline metrics per period (매출액,
  영업이익, 당기순이익 …). Parsed row-by-row through ``METRIC_MAP``.
* ``m.stock.naver.com/api/stock/{symbol}/trend`` — JSON list of 10 days of
  investor flow (외인 / 기관 / 개인 순매수).
* ``navercomp.wisereport.co.kr/v2/company/cF[3002|4002|9001|1001|5001]`` —
  JSON. The NaverComp WiseReport family. cF3002 is the 244-item statement
  ledger, cF4002 is the investment ratio grid, cF9001 is sector / market
  comparison, cF1001 is the summary HTML (skipped here), cF5001 is the
  consensus timeline.
* ``comp.fnguide.com/SVO2/ASP/SVD_Main.asp`` — HTML. Legacy fallback; hand
  parsed with BeautifulSoup.
* ``fchart.stock.naver.com/sise.nhn`` — XML. Optional 1000-day OHLCV backup.

All parsers return homogeneous dicts keyed by fields consumed in
``client.py``. Missing fields are ``None`` — never fabricated.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #


# Maps Naver finance/annual|quarter `rowList[].title` (Korean) → canonical key
# used in our `FinancialStatement` records. Multiple aliases may share one key
# so that older schema variants (ROE vs ROE(%)) still map.
METRIC_MAP: dict[str, str] = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "지배주주순이익": "net_income_controlling",
    "비지배주주순이익": "net_income_non_controlling",
    "영업이익률": "operating_margin",
    "순이익률": "net_margin",
    "ROE(%)": "roe",
    "ROE": "roe",
    "ROA(%)": "roa",
    "ROA": "roa",
    "부채비율": "debt_ratio",
    "당좌비율": "quick_ratio",
    "유보율": "retention_ratio",
    "EPS(원)": "eps",
    "EPS": "eps",
    "PER(배)": "per",
    "PER": "per",
    "BPS(원)": "bps",
    "BPS": "bps",
    "PBR(배)": "pbr",
    "PBR": "pbr",
    "주당배당금(원)": "dividend_per_share",
    "시가배당률(%)": "dividend_yield",
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
}

# cF9001 dt3 ITEM codes → canonical metric name.
CF9001_ITEM_MAP: dict[str, str] = {
    "1": "per",
    "2": "pbr",
    "3": "revenue_growth",
    "6": "debt_ratio",
    "7": "stock_return",
    "8": "dividend_yield",
    "9": "roe",
    "11": "gross_margin",
}


# --------------------------------------------------------------------------- #
# Naver mobile JSON — integration endpoint                                     #
# --------------------------------------------------------------------------- #


def parse_naver_integration(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse the mobile integration JSON payload into a snapshot dict.

    The real payload exposes (among other keys) ``totalInfos`` — a list of
    ``{code, key, value, valueDesc}`` tuples. We switch on the ``code`` field
    which is stable English (``per``, ``pbr``, ``eps``, ``bps``, ``roe``,
    ``dividendYieldRatio`` …). Korean-labelled variants are also handled for
    robustness against schema drift."""
    out: dict[str, Any] = _empty_snapshot("naver_mobile")

    totals = payload.get("totalInfos") or []
    for item in totals if isinstance(totals, list) else []:
        code = (item.get("code") or "").strip()
        key = (item.get("key") or "").strip()
        raw_value = item.get("value")
        value = _num(raw_value)
        if code == "per" or key == "PER":
            out["per"] = value
        elif code == "pbr" or key == "PBR":
            out["pbr"] = value
        elif code == "eps" or key == "EPS":
            out["eps"] = value
        elif code == "bps" or key == "BPS":
            out["bps"] = value
        elif code == "roe":
            out["roe"] = value
        elif code == "roa":
            out["roa"] = value
        elif code == "dividendYieldRatio" or key == "배당수익률":
            out["dividend_yield"] = value
        elif code == "foreignRate" or key in ("외인소진율", "외국인소진율"):
            out["foreign_ratio"] = value
        elif code == "highPriceOf52Weeks" or key == "52주 최고":
            out["high_52w"] = value
        elif code == "lowPriceOf52Weeks" or key == "52주 최저":
            out["low_52w"] = value
        elif code == "marketValue" or key == "시총":
            # marketValue comes back as "1,337조 3,362억" — use Korean parser.
            out["market_cap"] = parse_korean_currency(str(raw_value or ""))
            if raw_value:
                out["market_cap_raw"] = raw_value
        elif code == "cnsPer" or key == "추정PER":
            out["consensus_per"] = value
        elif code == "cnsEps" or key == "추정EPS":
            out["consensus_eps"] = value

    # Consensus block
    consensus = payload.get("consensusInfo")
    if isinstance(consensus, dict):
        out["target_price"] = _num(consensus.get("priceTargetMean"))
        rec = _num(consensus.get("recommMean"))
        if rec is not None:
            out["investment_opinion"] = _recomm_label(rec)
            out["consensus_recomm_score"] = rec

    # Peers — industryCompareInfo is a flat list of company dicts.
    peers_raw = payload.get("industryCompareInfo") or []
    if isinstance(peers_raw, list):
        peers: list[dict[str, Any]] = []
        for p in peers_raw:
            if not isinstance(p, dict):
                continue
            peers.append(
                {
                    "symbol": p.get("itemCode"),
                    "name": p.get("stockName"),
                    "close": _num(p.get("closePrice")),
                    "change_pct": _num(p.get("fluctuationsRatio")),
                    "market_cap": _num(p.get("marketValue")),
                    "per": _num(p.get("per")),
                    "pbr": _num(p.get("pbr")),
                    "roe": _num(p.get("roe")),
                }
            )
        out["peers"] = peers[:10]

    researches = payload.get("researches")
    if isinstance(researches, list) and researches:
        out["summary_notes"].append(f"Naver researches: {len(researches)}")

    name = payload.get("stockName")
    if name:
        out["summary_notes"].append(f"Naver: {name}")

    industry_code = payload.get("industryCode")
    if industry_code:
        out["industry_code"] = str(industry_code)

    return out


# --------------------------------------------------------------------------- #
# Naver mobile — finance/annual and finance/quarter                            #
# --------------------------------------------------------------------------- #


def parse_naver_finance(payload: dict[str, Any], period_type: str) -> list[dict[str, Any]]:
    """Parse finance/annual or finance/quarter into a list of period records.

    ``period_type`` is ``"annual"`` or ``"quarterly"``. Each returned record
    has shape::

        {
          "period": "2024" | "2024Q4",
          "period_type": "annual" | "quarterly",
          "is_estimate": False,
          "revenue": ...,
          "operating_income": ...,
          "net_income": ...,
          ...
        }

    Metrics come from METRIC_MAP — unknown titles are silently skipped.
    """
    info = payload.get("financeInfo") or {}
    periods = info.get("trTitleList") or []
    rows = info.get("rowList") or []
    if not isinstance(periods, list) or not isinstance(rows, list):
        return []

    # Build per-period accumulator, preserving input order.
    periods_out: list[dict[str, Any]] = []
    keys_order: list[str] = []
    for p in periods:
        if not isinstance(p, dict):
            continue
        key = str(p.get("key") or "").strip()
        if not key:
            continue
        is_consensus = (p.get("isConsensus") or "").strip() == "Y"
        period_str = _period_key(key, period_type)
        if not period_str:
            continue
        keys_order.append(key)
        periods_out.append(
            {
                "period": period_str,
                "period_type": period_type,
                "is_estimate": is_consensus,
            }
        )

    if not periods_out:
        return []

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (row.get("title") or "").strip()
        metric = METRIC_MAP.get(title)
        if not metric:
            continue
        columns = row.get("columns") or {}
        if not isinstance(columns, dict):
            continue
        for i, key in enumerate(keys_order):
            cell = columns.get(key) or {}
            val = _num(cell.get("value") if isinstance(cell, dict) else cell)
            if val is None:
                continue
            periods_out[i][metric] = val

    return periods_out


# --------------------------------------------------------------------------- #
# Naver mobile — trend (investor flow)                                         #
# --------------------------------------------------------------------------- #


def parse_naver_trend(payload: Any) -> list[dict[str, Any]]:
    """Parse the ``trend`` JSON list (10 days of investor flow).

    Returns a list of per-day dicts in chronological order (caller may reverse
    if newest-first is needed). Values follow the Korean convention that
    positive = net-buy, negative = net-sell. The numeric values are stripped
    of ``+``/``-`` signs by `_num`; we preserve sign by re-extracting with
    `_signed_num`.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        biz = str(item.get("bizdate") or "").strip()
        if not biz:
            continue
        out.append(
            {
                "date": biz,
                "foreign_net": _signed_num(item.get("foreignerPureBuyQuant")),
                "foreign_hold_ratio": _num(item.get("foreignerHoldRatio")),
                "institution_net": _signed_num(item.get("organPureBuyQuant")),
                "individual_net": _signed_num(item.get("individualPureBuyQuant")),
                "close": _num(item.get("closePrice")),
                "volume": _num(item.get("accumulatedTradingVolume")),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# NaverComp WiseReport — cF3002 (244-item statements)                          #
# --------------------------------------------------------------------------- #


def parse_wisereport_cf3002(payload: dict[str, Any], period_type: str) -> list[dict[str, Any]]:
    """Parse the ``cF3002`` ledger into per-period headline financials.

    The raw response has up to 244 account rows × 6 period columns — far too
    granular for a modal. We extract just the headline P&L accounts (매출액,
    영업이익, 당기순이익 and equity/asset totals) which is what the UI
    actually renders. Every unknown account is silently skipped.
    """
    if not isinstance(payload, dict):
        return []
    periods_raw = payload.get("YYMM") or []
    rows = payload.get("DATA") or []
    if not isinstance(periods_raw, list) or not isinstance(rows, list):
        return []

    # Parse period labels: "2024/12 (IFRS연결)" → "2024", "2024/12(E)" → est.
    parsed_periods: list[tuple[str, bool]] = []
    for p in periods_raw:
        clean = re.sub(r"<br\s*/?>", " ", str(p)).strip()
        if "전년대비" in clean or "YoY" in clean:
            # YoY marker columns — not a period, skip.
            parsed_periods.append(("", False))
            continue
        is_est = "(E)" in clean
        m = re.match(r"(\d{4})/(\d{2})", clean)
        if m:
            year, month_str = m.group(1), m.group(2)
            if period_type == "annual":
                parsed_periods.append((year, is_est))
            else:
                month = int(month_str)
                q = (month - 1) // 3 + 1
                parsed_periods.append((f"{year}Q{q}", is_est))
        else:
            parsed_periods.append(("", is_est))

    # Accumulator keyed by period.
    by_period: dict[str, dict[str, Any]] = {}
    target_accounts = {
        "매출액": "revenue",
        "매출액(수익)": "revenue",
        "영업이익": "operating_income",
        "영업이익(발표기준)": "operating_income_reported",
        "당기순이익": "net_income",
        "(지배주주지분)당기순이익": "net_income_controlling",
        "자산총계": "total_assets",
        "부채총계": "total_liabilities",
        "자본총계": "total_equity",
        "세전계속사업이익": "pretax_income",
        "법인세비용": "tax_expense",
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        acc_name = (row.get("ACC_NM") or "").strip()
        metric = target_accounts.get(acc_name)
        if not metric:
            continue
        for i, (period_str, is_est) in enumerate(parsed_periods):
            if not period_str:
                continue
            val = row.get(f"DATA{i + 1}")
            if val is None:
                continue
            try:
                val_f = float(val)
            except (ValueError, TypeError):
                continue
            rec = by_period.setdefault(
                period_str,
                {
                    "period": period_str,
                    "period_type": period_type,
                    "is_estimate": is_est,
                },
            )
            # Prefer non-overlapping keys; don't overwrite headline revenue
            # with operating_income_reported aliases etc.
            rec.setdefault(metric, val_f)

    # Stable newest-first ordering — period strings sort lexicographically.
    return sorted(by_period.values(), key=lambda r: r["period"], reverse=True)


# --------------------------------------------------------------------------- #
# NaverComp WiseReport — cF4002 (investment metrics grid)                      #
# --------------------------------------------------------------------------- #


def parse_wisereport_cf4002(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``cF4002`` into per-period metric records.

    Extracts gross/op/net margin, EBITDA margin, ROE, ROA, ROIC. One record
    per period; missing metrics are omitted. Returns newest-first."""
    if not isinstance(payload, dict):
        return []
    periods_raw = payload.get("YYMM") or []
    rows = payload.get("DATA") or []
    if not isinstance(periods_raw, list) or not isinstance(rows, list):
        return []

    periods: list[str] = []
    is_est_list: list[bool] = []
    for p in periods_raw:
        clean = re.sub(r"<br\s*/?>", " ", str(p)).strip()
        is_est = "(E)" in clean
        m = re.match(r"(\d{4})", clean)
        if m:
            periods.append(m.group(1))
            is_est_list.append(is_est)
        else:
            periods.append("")
            is_est_list.append(False)

    metric_keys = {
        "매출총이익률": "gross_margin",
        "영업이익률": "operating_margin",
        "순이익률": "net_margin",
        "EBITDA마진율": "ebitda_margin",
        "ROE": "roe",
        "ROA": "roa",
        "ROIC": "roic",
    }
    by_period: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = (row.get("ACC_NM") or "").strip()
        key = None
        for label, k in metric_keys.items():
            if name == label or name.startswith(label):
                key = k
                break
        if not key:
            continue
        for i, period_str in enumerate(periods):
            if not period_str:
                continue
            val = row.get(f"DATA{i + 1}")
            if val is None:
                continue
            try:
                val_f = float(val)
            except (ValueError, TypeError):
                continue
            rec = by_period.setdefault(
                period_str,
                {"period": period_str, "is_estimate": is_est_list[i]},
            )
            rec[key] = val_f

    return sorted(by_period.values(), key=lambda r: r["period"], reverse=True)


# --------------------------------------------------------------------------- #
# NaverComp WiseReport — cF9001 (sector/market comparison)                     #
# --------------------------------------------------------------------------- #


def parse_wisereport_cf9001(payload: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    """Parse ``cF9001`` sector comparison data.

    Returns a mapping from metric name → ``{company, sector, market}`` triple
    (latest actual period, ``FY0``). Use cases: plot a company's ROE against
    its sector and the KOSPI in one chart.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, float | None]] = {}

    # Prefer dt3 (valuation comparison) — falls back to dt0 (growth/return).
    for key in ("dt3", "dt0"):
        block = payload.get(key)
        if not isinstance(block, dict):
            continue
        for row in block.get("data", []):
            if not isinstance(row, dict):
                continue
            item = str(row.get("ITEM", "")).strip()
            gubn = str(row.get("GUBN", "")).strip()
            metric = CF9001_ITEM_MAP.get(item)
            if not metric:
                continue
            val = _num(row.get("FY0"))
            if val is None:
                continue
            bucket = out.setdefault(metric, {"company": None, "sector": None, "market": None})
            if gubn == "1":
                bucket["company"] = val
            elif gubn == "2":
                bucket["sector"] = val
            elif gubn == "3":
                bucket["market"] = val

    return out


# --------------------------------------------------------------------------- #
# NaverComp WiseReport — cF1001 / ownership (HTML)                             #
# --------------------------------------------------------------------------- #


def parse_wisereport_ownership(html: str) -> dict[str, Any]:
    """Scrape the c1010001 / cF1001 ownership summary table.

    Returns ``{major_shareholder_name, major_shareholder_pct, float_ratio,
    shares_outstanding, beta_52w}`` — any missing fields are None.
    """
    out: dict[str, Any] = {
        "major_shareholder_name": None,
        "major_shareholder_pct": None,
        "float_ratio": None,
        "shares_outstanding": None,
        "beta_52w": None,
    }
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:  # pragma: no cover
        return out

    soup = BeautifulSoup(html, "html.parser")

    # Shares outstanding / float ratio are in the cTB11 two-column table.
    for tr in soup.select("#cTB11 tr"):
        th = tr.find("th")
        if not th:
            continue
        label = th.get_text(" ", strip=True)
        tds = tr.find_all("td")
        if not tds:
            continue
        val = tds[-1].get_text(" ", strip=True)
        if "52주베타" in label:
            out["beta_52w"] = _num(val)
        elif "발행주식수" in label:
            # "5,846,278,608주 / 75.23%" → split on /
            parts = val.split("/")
            if parts:
                out["shares_outstanding"] = _num(parts[0])
            if len(parts) > 1:
                out["float_ratio"] = _num(parts[1])
        elif "외국인지분율" in label and out.get("major_shareholder_pct") is None:
            # Foreign ratio — we capture this elsewhere, ignore here.
            pass

    # Major shareholder table — #cTB13 or class ".us_table_ty1".
    holder_tbl = soup.select_one("#cTB13") or soup.select_one(".us_table_ty1")
    if holder_tbl:
        first = holder_tbl.select_one("tbody tr")
        if first:
            cells = [c.get_text(" ", strip=True) for c in first.find_all(["td", "th"])]
            if len(cells) >= 2:
                out["major_shareholder_name"] = _dedupe_name(cells[0])[:64]
                out["major_shareholder_pct"] = _num(cells[-1])

    return out


def _dedupe_name(raw: str) -> str:
    """The NaverComp shareholder table emits the name twice (tooltip + label).

    Collapse ``"X X"`` where both halves are identical into a single ``"X"``.
    """
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not s:
        return s
    half = len(s) // 2
    # Try an exact split. Tolerate 1 char of padding whitespace drift.
    for boundary in (half, half + 1, half - 1):
        if boundary <= 0 or boundary >= len(s):
            continue
        left = s[:boundary].strip()
        right = s[boundary:].strip()
        if left and left == right:
            return left
    return s


# --------------------------------------------------------------------------- #
# fchart XML (1000-day OHLCV backup)                                           #
# --------------------------------------------------------------------------- #


def parse_fchart_xml(xml_str: str) -> list[dict[str, Any]]:
    """Parse an ``fchart.stock.naver.com/sise.nhn`` XML payload.

    Each ``<item data="YYYYMMDD|open|high|low|close|volume" />`` becomes a
    dict. Malformed rows are dropped silently. Not currently wired into the
    API response; here so the adapter stays feature-complete."""
    rows: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return rows
    for item in root.findall("item"):
        data = item.get("data") or ""
        parts = data.split("|")
        if len(parts) < 6:
            continue
        try:
            rows.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": float(parts[5]),
                }
            )
        except (ValueError, TypeError):
            continue
    return rows


# --------------------------------------------------------------------------- #
# comp.fnguide.com HTML fallback                                               #
# --------------------------------------------------------------------------- #


def parse_fnguide_html(html: str) -> dict[str, Any]:
    """Parse the fnguide Snapshot page HTML. BeautifulSoup-powered; very
    defensive — missing tables leave fields as None."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:  # pragma: no cover
        return {"source": "fnguide", "summary_notes": ["bs4 missing"]}

    out: dict[str, Any] = _empty_snapshot("fnguide")

    soup = BeautifulSoup(html, "html.parser")

    # Snapshot summary tables — FnGuide exposes key stats inside dl.dlst.
    for dl in soup.select("dl.dlst"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        key = dt.get_text(" ", strip=True)
        val = dd.get_text(" ", strip=True)
        num = _num(val)
        if "PER" in key and out["per"] is None:
            out["per"] = num
        elif "PBR" in key and out["pbr"] is None:
            out["pbr"] = num
        elif "EPS" in key and out["eps"] is None:
            out["eps"] = num
        elif "BPS" in key and out["bps"] is None:
            out["bps"] = num
        elif "ROE" in key:
            out["roe"] = num
        elif "ROA" in key:
            out["roa"] = num
        elif "부채비율" in key:
            out["debt_ratio"] = num
        elif "배당수익률" in key:
            out["dividend_yield"] = num
        elif "외국인" in key:
            out["foreign_ratio"] = num
        elif "시가총액" in key:
            out["market_cap"] = num

    # Target price — comp.fnguide renders inside #corp_group2 "적정주가".
    for row in soup.select("#corp_group2 tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        label = th.get_text(" ", strip=True)
        if "목표주가" in label or "적정주가" in label:
            out["target_price"] = _num(td.get_text(" ", strip=True))
        elif "투자의견" in label:
            out["investment_opinion"] = td.get_text(" ", strip=True)[:64]

    # Major shareholder table (#cTB13) — best-effort scrape.
    holder_tbl = soup.select_one("#cTB13") or soup.select_one(".us_table_ty1")
    if holder_tbl:
        first = holder_tbl.select_one("tbody tr")
        if first:
            cells = [c.get_text(" ", strip=True) for c in first.find_all(["td", "th"])]
            if len(cells) >= 2:
                out["major_shareholder_name"] = cells[0][:64]
                out["major_shareholder_pct"] = _num(cells[-1])

    # Quarterly highlights — HTML tables with class highlight_B.
    fin_tbl = soup.select_one(".highlight_B") or soup.select_one("#highlight_D_Q")
    if fin_tbl:
        headers = [th.get_text(" ", strip=True) for th in fin_tbl.select("thead th")]
        row_labels: dict[str, list[float | None]] = {}
        for tr in fin_tbl.select("tbody tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            label = cells[0].get_text(" ", strip=True)
            vals = [_num(c.get_text(" ", strip=True)) for c in cells[1:]]
            row_labels[label] = vals
        periods = headers[1:] if headers else []
        quarterly: list[dict[str, Any]] = []
        for i, p in enumerate(periods[:4]):
            quarterly.append(
                {
                    "period": p,
                    "period_type": "quarterly",
                    "revenue":          _pick(row_labels, ["매출액"], i),
                    "operating_income": _pick(row_labels, ["영업이익"], i),
                    "net_income":       _pick(row_labels, ["당기순이익"], i),
                    "eps":              _pick(row_labels, ["EPS"], i),
                    "bps":              _pick(row_labels, ["BPS"], i),
                    "roe":              _pick(row_labels, ["ROE"], i),
                    "debt_ratio":       _pick(row_labels, ["부채비율"], i),
                }
            )
        out["financials_quarterly"] = quarterly

    out["summary_notes"].append("fnguide html snapshot parsed")
    return out


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #


_SYMBOL_RE = re.compile(r"^\d{6}$")


def is_valid_symbol(symbol: str) -> bool:
    return bool(_SYMBOL_RE.match(symbol or ""))


def parse_korean_currency(text: str) -> int | None:
    """Parse "1,102조 2,366억" → 110,223,660,000,000 (int KRW).

    Returns None when nothing parseable is found. 조 = 10^12, 억 = 10^8,
    만 = 10^4. The common 백만 alias (million) is also handled."""
    if not text:
        return None
    s = str(text).strip()
    total = 0
    matched = False
    unit_order = [("조", 1_000_000_000_000), ("억", 100_000_000), ("백만", 1_000_000), ("만", 10_000)]
    for unit, mult in unit_order:
        m = re.search(rf"([\d,\.]+)\s*{unit}", s)
        if m:
            try:
                total += int(float(m.group(1).replace(",", "")) * mult)
                matched = True
                s = s.replace(m.group(0), "", 1).strip()
            except ValueError:
                continue
    if matched:
        return total
    # Fall back to a plain integer parse.
    try:
        cleaned = re.sub(r"[^\d\-\.]", "", str(text))
        return int(float(cleaned)) if cleaned else None
    except (ValueError, TypeError):
        return None


def _empty_snapshot(source: str) -> dict[str, Any]:
    """Return a snapshot dict with every field initialised to None / []."""
    return {
        "source": source,
        "target_price": None,
        "investment_opinion": None,
        "consensus_recomm_score": None,
        "consensus_per": None,
        "consensus_eps": None,
        "per": None,
        "pbr": None,
        "eps": None,
        "bps": None,
        "roe": None,
        "roa": None,
        "debt_ratio": None,
        "dividend_yield": None,
        "market_cap": None,
        "foreign_ratio": None,
        "high_52w": None,
        "low_52w": None,
        "major_shareholder_pct": None,
        "major_shareholder_name": None,
        "industry_code": None,
        "financials_quarterly": [],
        "financials_annual": [],
        "financial_metrics": [],
        "sector_comparison": {},
        "investor_trend": [],
        "peers": [],
        "summary_notes": [],
    }


def _recomm_label(rec: float) -> str:
    """Naver recommMean scale — 5=Strong Buy, 1=Sell (higher is more bullish)."""
    if rec >= 4.5:
        return "Strong Buy"
    if rec >= 3.5:
        return "Buy"
    if rec >= 2.5:
        return "Hold"
    if rec >= 1.5:
        return "Reduce"
    return "Sell"


def _num(v: Any) -> float | None:
    """Best-effort numeric parse, unsigned. Strips Korean units & commas."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in ("-", "N/A", "—"):
        return None
    s = s.replace(",", "").replace("%", "").replace("+", "")
    s = re.sub(r"[배원주]+\s*$", "", s)
    s = s.strip()
    # Korean unit suffix parsing (via parse_korean_currency).
    if any(u in s for u in ("조", "억", "백만", "만")):
        parsed = parse_korean_currency(s)
        return float(parsed) if parsed is not None else None
    try:
        return float(s)
    except ValueError:
        return None


def _signed_num(v: Any) -> float | None:
    """Like _num, but preserves +/- sign from strings like "+516,352" or "-4,955,613"."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in ("-", "N/A", "—"):
        return None
    sign = 1.0
    if s.startswith("-"):
        sign = -1.0
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    n = _num(s)
    return sign * n if n is not None else None


def _pick(row_labels: dict[str, list[float | None]], keys: list[str], idx: int) -> float | None:
    for k in keys:
        for label, vals in row_labels.items():
            if k in label and idx < len(vals):
                return vals[idx]
    return None


def _period_key(key: str, period_type: str) -> str:
    """Convert Naver period key ("202412" / "202503") into our format.

    Annual: take first 4 chars. Quarterly: year + Q{1..4} from month."""
    if not key:
        return ""
    if period_type == "annual":
        return key[:4]
    if len(key) < 6:
        return key[:4]
    try:
        year = key[:4]
        month = int(key[4:6])
        q = (month - 1) // 3 + 1
        return f"{year}Q{q}"
    except ValueError:
        return key[:4]
