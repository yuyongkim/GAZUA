"""Naver / FnGuide snapshot client — 3-domain merged fundamentals.

Public surface
--------------
``FnguideAdapter.get_snapshot(symbol)``    — fast single-shot (Mobile → fallback).
``FnguideAdapter.get_full_snapshot(symbol)`` — enriched bundle: Mobile +
NaverComp (cF3002 / cF4002 / cF9001 / ownership) + investor trend. Mobile and
NaverComp are fetched concurrently via a 2-worker ``ThreadPoolExecutor``.

Endpoints
---------
Mobile API (JSON, primary):
    GET https://m.stock.naver.com/api/stock/{symbol}/integration
    GET https://m.stock.naver.com/api/stock/{symbol}/finance/{annual|quarter}
    GET https://m.stock.naver.com/api/stock/{symbol}/trend

NaverComp / WiseReport (JSON, rich):
    GET https://navercomp.wisereport.co.kr/v2/company/cF3002.aspx  (244 accounts)
    GET https://navercomp.wisereport.co.kr/v2/company/cF4002.aspx  (ratios)
    GET https://navercomp.wisereport.co.kr/company/ajax/cF9001.aspx (sector)
    GET https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx (HTML, ownership)

    encparam is a per-session token: we hit the parent HTML first, parse the
    token from the returned page, and reuse the same ``requests.Session`` (for
    cookies) on every downstream AJAX call.

Fallback (HTML, legacy):
    GET https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp

Policy
------
* Per-symbol 10 minute cache (caller-supplied repository).
* Polite: one session per symbol; desktop UA; no bulk scraping.
* Silent on partial failure — if any sub-source errors, we return whatever
  we have with ``degraded=True``.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import httpx

from ky_adapters.base import AdapterError, BaseAdapter
from ky_adapters.naver_fnguide.parser import (
    is_valid_symbol,
    parse_fchart_xml,
    parse_fnguide_html,
    parse_naver_finance,
    parse_naver_integration,
    parse_naver_trend,
    parse_wisereport_cf3002,
    parse_wisereport_cf4002,
    parse_wisereport_cf9001,
    parse_wisereport_ownership,
)

logger = logging.getLogger(__name__)

NAVER_BASE = "https://m.stock.naver.com/api/stock"
NAVER_INTEGRATION = NAVER_BASE + "/{symbol}/integration"
NAVER_FINANCE_ANNUAL = NAVER_BASE + "/{symbol}/finance/annual"
NAVER_FINANCE_QUARTER = NAVER_BASE + "/{symbol}/finance/quarter"
NAVER_TREND = NAVER_BASE + "/{symbol}/trend"

WISE_BASE = "https://navercomp.wisereport.co.kr"
WISE_PARENT = WISE_BASE + "/v2/company/c1010001.aspx?cmp_cd={symbol}"
WISE_CF3002 = WISE_BASE + "/v2/company/cF3002.aspx"
WISE_CF4002 = WISE_BASE + "/v2/company/cF4002.aspx"
WISE_CF9001 = WISE_BASE + "/company/ajax/cF9001.aspx"

FNGUIDE_URL = (
    "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
    "?pGB=1&gicode=A{symbol}&cID=&MenuYn=Y&ReportGB=&NewMenuID=11&stkGb=701"
)

FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"

# Browser-ish UA avoids trivial bot blocks; we stay well under any rate limit.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# encparam pattern: `encparam: 'BASE64TOKEN'` inside the c1010001 page JS.
ENCPARAM_RE = re.compile(r"encparam[^a-zA-Z0-9]*([A-Za-z0-9+/=]{10,})")


# --------------------------------------------------------------------------- #
# Snapshot dataclass                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class FnguideSnapshot:
    """Normalised fundamentals snapshot.

    Keeps backward compatibility with the original (Mobile-only) fields while
    adding richer NaverComp collections. The UI may ignore the richer fields;
    the REST envelope includes every field regardless."""

    symbol: str
    fetched_at: float                       # unix seconds
    source: str                             # "naver_mobile" | "fnguide" | "mixed" | "full"
    degraded: bool = False
    # ---- Valuation / consensus ------------------------------------------
    target_price: float | None = None
    investment_opinion: str | None = None
    consensus_recomm_score: float | None = None
    consensus_per: float | None = None
    consensus_eps: float | None = None
    per: float | None = None
    pbr: float | None = None
    eps: float | None = None
    bps: float | None = None
    roe: float | None = None
    roa: float | None = None
    debt_ratio: float | None = None
    dividend_yield: float | None = None
    market_cap: float | None = None
    market_cap_raw: str | None = None
    foreign_ratio: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    industry_code: str | None = None
    # ---- Ownership --------------------------------------------------------
    major_shareholder_name: str | None = None
    major_shareholder_pct: float | None = None
    float_ratio: float | None = None
    shares_outstanding: float | None = None
    beta_52w: float | None = None
    # ---- Collections ------------------------------------------------------
    financials_quarterly: list[dict[str, Any]] = field(default_factory=list)
    financials_annual: list[dict[str, Any]] = field(default_factory=list)
    financial_metrics: list[dict[str, Any]] = field(default_factory=list)
    sector_comparison: dict[str, dict[str, float | None]] = field(default_factory=dict)
    investor_trend: list[dict[str, Any]] = field(default_factory=list)
    peers: list[dict[str, Any]] = field(default_factory=list)
    summary_notes: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "degraded": self.degraded,
            "target_price": self.target_price,
            "investment_opinion": self.investment_opinion,
            "consensus_recomm_score": self.consensus_recomm_score,
            "consensus_per": self.consensus_per,
            "consensus_eps": self.consensus_eps,
            "per": self.per,
            "pbr": self.pbr,
            "eps": self.eps,
            "bps": self.bps,
            "roe": self.roe,
            "roa": self.roa,
            "debt_ratio": self.debt_ratio,
            "dividend_yield": self.dividend_yield,
            "market_cap": self.market_cap,
            "market_cap_raw": self.market_cap_raw,
            "foreign_ratio": self.foreign_ratio,
            "high_52w": self.high_52w,
            "low_52w": self.low_52w,
            "industry_code": self.industry_code,
            "major_shareholder_name": self.major_shareholder_name,
            "major_shareholder_pct": self.major_shareholder_pct,
            "float_ratio": self.float_ratio,
            "shares_outstanding": self.shares_outstanding,
            "beta_52w": self.beta_52w,
            "financials_quarterly": self.financials_quarterly,
            "financials_annual": self.financials_annual,
            "financial_metrics": self.financial_metrics,
            "sector_comparison": self.sector_comparison,
            "investor_trend": self.investor_trend,
            "peers": self.peers,
            "summary_notes": self.summary_notes,
            "sources_used": self.sources_used,
        }


# --------------------------------------------------------------------------- #
# Adapter                                                                      #
# --------------------------------------------------------------------------- #


class FnguideAdapter(BaseAdapter):
    """Combined Naver + FnGuide + NaverComp snapshot fetcher."""

    source_id = "naver_fnguide"
    priority = 5

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        super().__init__(client=client)

    @classmethod
    def from_settings(cls) -> "FnguideAdapter":
        return cls()

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            resp = self._request(
                "GET",
                NAVER_INTEGRATION.format(symbol="005930"),
                headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"},
                timeout=5.0,
                retries=1,
            )
            if resp.status_code != 200:
                return self._timed_fail(self.source_id, f"naver HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        return self._timed_ok(
            (time.perf_counter() - t0) * 1000,
            self.source_id,
            {"probe": "naver_integration"},
        )

    # --------- Public surface ---------

    def get_snapshot(self, symbol: str) -> FnguideSnapshot:
        """Fast Mobile-only snapshot (with FnGuide HTML fallback).

        Kept for backward compatibility with callers that don't need the full
        NaverComp/trend payload and want the lowest possible latency.
        """
        if not is_valid_symbol(symbol):
            raise AdapterError(f"invalid KRX symbol: {symbol!r}")

        primary = self._try_naver_integration(symbol)
        if primary and _covers_core(primary):
            return _as_snapshot(symbol, primary, source="naver_mobile", sources_used=["naver_integration"])

        fallback = self._try_fnguide(symbol)
        if primary and fallback:
            merged = _merge(primary, fallback)
            return _as_snapshot(
                symbol, merged, source="mixed",
                sources_used=["naver_integration", "fnguide_html"],
            )
        if primary:
            return _as_snapshot(
                symbol, primary, source="naver_mobile", degraded=True,
                sources_used=["naver_integration"],
            )
        if fallback:
            return _as_snapshot(
                symbol, fallback, source="fnguide", degraded=True,
                sources_used=["fnguide_html"],
            )

        logger.warning("fnguide: both naver and fnguide failed for %s", symbol)
        return FnguideSnapshot(
            symbol=symbol,
            fetched_at=time.time(),
            source="none",
            degraded=True,
            summary_notes=["no snapshot sources available"],
        )

    def get_full_snapshot(self, symbol: str) -> FnguideSnapshot:
        """Enriched snapshot — DB-first, then Mobile + NaverComp live.

        Lookup order (Company_Credit migration landed a broad cold cache so
        we always try the DB first):

        1. ``Repository.get_fnguide_cached(max_age_hours=24)`` — return
           immediately with ``source="db_fresh"``. No network.
        2. 24h < age <= 7d (stale): return the stale payload AND fire a
           background refresh. ``source="db_stale"`` and ``degraded=False``
           because we have coherent data.
        3. Age > 7d or no row: full live fetch (Mobile + NaverComp),
           upsert, return as ``source="live_fresh"``.
        4. Live fetch failed but a stale row exists: return the stale row
           with ``degraded=True``.

        Returns even when individual sub-sources fail; ``degraded=True``
        marks when at least one source was missed. The live-path merges:

        * Mobile integration (valuation summary + peers)
        * Mobile finance/annual + finance/quarter (headline financials)
        * Mobile trend (10-day investor flow)
        * NaverComp cF3002 (annual + quarterly statements)
        * NaverComp cF4002 (margin ratios)
        * NaverComp cF9001 (sector / market comparison)
        * NaverComp c1010001 (ownership details: beta, shares, float)
        """
        if not is_valid_symbol(symbol):
            raise AdapterError(f"invalid KRX symbol: {symbol!r}")

        # --- DB-first fallback ------------------------------------------------
        # Attempt repository lookup; tolerant of storage failures so tests that
        # stub the adapter stay green.
        cached_payload: dict[str, Any] | None = None
        cached_age_hours: float | None = None
        try:
            from ky_core.storage import Repository
            repo_db = Repository()
            row = repo_db.get_fnguide_snapshot(symbol)
            if row:
                cached_age_hours = repo_db.get_fnguide_age_hours(symbol)
                try:
                    import json as _json
                    cached_payload = _json.loads(row["payload"])
                except Exception:  # noqa: BLE001
                    cached_payload = None
        except Exception:  # noqa: BLE001
            logger.debug("fnguide: DB-first lookup skipped (storage unavailable)",
                         exc_info=True)

        if cached_payload and cached_age_hours is not None and cached_age_hours <= 24.0:
            # Fresh — serve it, zero network.
            return _payload_to_snapshot(cached_payload, source="db_fresh")

        if cached_payload and cached_age_hours is not None and cached_age_hours <= 24.0 * 7:
            # Stale but usable — serve AND fire a background refresh. The
            # refresh is fire-and-forget; the current request stays fast.
            self._schedule_background_refresh(symbol)
            return _payload_to_snapshot(cached_payload, source="db_stale")

        # --- Live path --------------------------------------------------------

        sources_used: list[str] = []
        errors: list[str] = []

        # Two top-level workers: Mobile bundle + NaverComp bundle run concurrently.
        mobile_result: dict[str, Any] = {}
        comp_result: dict[str, Any] = {}

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self._collect_mobile, symbol): "mobile",
                pool.submit(self._collect_navercomp, symbol): "navercomp",
            }
            for f in as_completed(futures):
                label = futures[f]
                try:
                    data = f.result()
                except Exception as exc:  # noqa: BLE001
                    logger.info("fnguide: %s bundle failed for %s: %s", label, symbol, exc)
                    errors.append(f"{label}:{type(exc).__name__}")
                    continue
                if label == "mobile":
                    mobile_result = data or {}
                else:
                    comp_result = data or {}

        if mobile_result:
            sources_used.extend(mobile_result.get("sources_used", []))
        if comp_result:
            sources_used.extend(comp_result.get("sources_used", []))

        # Merge: Mobile integration is the base, NaverComp extends.
        base = mobile_result.get("integration") or {}
        if not base and comp_result:
            # Mobile failed entirely — use comp-only data + FnGuide fallback.
            fallback = self._try_fnguide(symbol)
            if fallback:
                base = fallback
                sources_used.append("fnguide_html")

        merged = dict(base) if base else _skeleton()

        # Inject Mobile finance bundles (headline 16-metric rows).
        if mobile_result.get("financials_quarterly"):
            merged["financials_quarterly"] = mobile_result["financials_quarterly"]
        if mobile_result.get("financials_annual"):
            merged["financials_annual"] = mobile_result["financials_annual"]
        if mobile_result.get("investor_trend"):
            merged["investor_trend"] = mobile_result["investor_trend"]

        # NaverComp data wins for granular statements / sector comparisons —
        # but only if it actually produced rows (otherwise keep Mobile data).
        if comp_result.get("financials_annual"):
            merged["financials_annual"] = _prefer_richer(
                comp_result["financials_annual"],
                merged.get("financials_annual") or [],
            )
        if comp_result.get("financials_quarterly"):
            merged["financials_quarterly"] = _prefer_richer(
                comp_result["financials_quarterly"],
                merged.get("financials_quarterly") or [],
            )
        if comp_result.get("financial_metrics"):
            merged["financial_metrics"] = comp_result["financial_metrics"]
        if comp_result.get("sector_comparison"):
            merged["sector_comparison"] = comp_result["sector_comparison"]

        # Ownership: fill from NaverComp c1010001 HTML when present.
        ownership = comp_result.get("ownership") or {}
        for k in (
            "major_shareholder_name",
            "major_shareholder_pct",
            "float_ratio",
            "shares_outstanding",
            "beta_52w",
        ):
            if ownership.get(k) is not None and merged.get(k) in (None, ""):
                merged[k] = ownership[k]

        # Backfill headline ROE/ROA/debt_ratio from the latest annual record if
        # the integration endpoint didn't expose them (it usually doesn't — PER
        # / EPS are in `totalInfos`, but ROE lives under finance/annual).
        for headline, key in (("roe", "roe"), ("roa", "roa"), ("debt_ratio", "debt_ratio")):
            if merged.get(headline) is None:
                for rec in merged.get("financials_annual") or []:
                    if rec.get("is_estimate"):
                        continue
                    if rec.get(key) is not None:
                        merged[headline] = rec[key]
                        break

        degraded = bool(errors) or not base
        source = "live_fresh" if (mobile_result and comp_result) else (
            "naver_mobile" if mobile_result else ("fnguide" if base else "none")
        )

        if errors:
            merged.setdefault("summary_notes", []).append(
                "partial: " + ",".join(errors)
            )

        # Live fetch failed entirely *and* we have a stale row → serve stale
        # degraded rather than returning an empty skeleton.
        if source == "none" and cached_payload:
            return _payload_to_snapshot(
                cached_payload, source="db_stale", degraded=True,
            )

        snap = _as_snapshot(
            symbol, merged, source=source, degraded=degraded,
            sources_used=sources_used,
        )

        # Persist the fresh payload so subsequent callers hit DB-first. The
        # caller (REST endpoint) also persists — this is a safety net for
        # non-REST callers (collect scripts, notebooks).
        if source in ("live_fresh", "naver_mobile") and not degraded:
            try:
                from ky_core.storage import Repository
                import json as _json
                Repository().upsert_fnguide_snapshot(
                    symbol,
                    _json.dumps(snap.to_dict(), ensure_ascii=False),
                    source=snap.source,
                    degraded=snap.degraded,
                )
            except Exception:  # noqa: BLE001
                logger.debug("fnguide: post-fetch persist skipped", exc_info=True)

        return snap

    def _schedule_background_refresh(self, symbol: str) -> None:
        """Fire-and-forget refresh for stale DB rows. Uses a daemon thread so
        the response returns immediately; we don't wait on the result.
        """
        import threading

        def _refresh() -> None:
            try:
                from ky_core.storage import Repository
                import json as _json
                # Directly call the live collectors without DB-first to avoid
                # recursion. We reuse ``_collect_mobile`` + ``_collect_navercomp``
                # and merge minimally; the full merged snapshot is acceptable.
                mobile_result = self._collect_mobile(symbol) or {}
                comp_result = self._collect_navercomp(symbol) or {}
                base = mobile_result.get("integration") or {}
                if not base:
                    return
                merged = dict(base)
                if mobile_result.get("financials_quarterly"):
                    merged["financials_quarterly"] = mobile_result["financials_quarterly"]
                if mobile_result.get("financials_annual"):
                    merged["financials_annual"] = mobile_result["financials_annual"]
                if mobile_result.get("investor_trend"):
                    merged["investor_trend"] = mobile_result["investor_trend"]
                if comp_result.get("financial_metrics"):
                    merged["financial_metrics"] = comp_result["financial_metrics"]
                if comp_result.get("sector_comparison"):
                    merged["sector_comparison"] = comp_result["sector_comparison"]
                snap = _as_snapshot(
                    symbol, merged, source="live_fresh", degraded=False,
                    sources_used=(mobile_result.get("sources_used", []) +
                                  comp_result.get("sources_used", [])),
                )
                Repository().upsert_fnguide_snapshot(
                    symbol,
                    _json.dumps(snap.to_dict(), ensure_ascii=False),
                    source=snap.source, degraded=snap.degraded,
                )
            except Exception:  # noqa: BLE001
                logger.debug("fnguide: background refresh failed for %s",
                             symbol, exc_info=True)

        threading.Thread(target=_refresh, name=f"fnguide-refresh-{symbol}",
                         daemon=True).start()

    # --------- Mobile bundle ---------

    def _collect_mobile(self, symbol: str) -> dict[str, Any]:
        """Fetch integration + finance/annual + finance/quarter + trend."""
        out: dict[str, Any] = {"sources_used": []}
        integration = self._try_naver_integration(symbol)
        if integration:
            out["integration"] = integration
            out["sources_used"].append("naver_integration")

        annual = self._try_naver_finance(symbol, period_type="annual")
        if annual:
            out["financials_annual"] = annual
            out["sources_used"].append("naver_finance_annual")

        quarter = self._try_naver_finance(symbol, period_type="quarterly")
        if quarter:
            out["financials_quarterly"] = quarter
            out["sources_used"].append("naver_finance_quarter")

        trend = self._try_naver_trend(symbol)
        if trend:
            out["investor_trend"] = trend
            out["sources_used"].append("naver_trend")

        return out

    def _try_naver_integration(self, symbol: str) -> dict[str, Any] | None:
        payload = self._fetch_json(
            NAVER_INTEGRATION.format(symbol=symbol),
            referer="https://m.stock.naver.com/",
            label="naver_integration",
            symbol=symbol,
        )
        if not isinstance(payload, dict):
            return None
        try:
            return parse_naver_integration(payload)
        except Exception:  # noqa: BLE001
            logger.exception("fnguide: naver integration parse failed for %s", symbol)
            return None

    def _try_naver_finance(self, symbol: str, *, period_type: str) -> list[dict[str, Any]] | None:
        url = (NAVER_FINANCE_ANNUAL if period_type == "annual" else NAVER_FINANCE_QUARTER).format(
            symbol=symbol
        )
        payload = self._fetch_json(
            url,
            referer="https://m.stock.naver.com/",
            label=f"naver_finance_{period_type}",
            symbol=symbol,
        )
        if not isinstance(payload, dict):
            return None
        try:
            return parse_naver_finance(payload, period_type)
        except Exception:  # noqa: BLE001
            logger.exception(
                "fnguide: naver finance parse failed for %s (%s)", symbol, period_type
            )
            return None

    def _try_naver_trend(self, symbol: str) -> list[dict[str, Any]] | None:
        payload = self._fetch_json(
            NAVER_TREND.format(symbol=symbol),
            referer="https://m.stock.naver.com/",
            label="naver_trend",
            symbol=symbol,
        )
        if payload is None:
            return None
        try:
            return parse_naver_trend(payload)
        except Exception:  # noqa: BLE001
            logger.exception("fnguide: naver trend parse failed for %s", symbol)
            return None

    # --------- NaverComp bundle ---------

    def _collect_navercomp(self, symbol: str) -> dict[str, Any]:
        """Fetch encparam + cF3002 (Y/Q) + cF4002 + cF9001 + ownership HTML."""
        out: dict[str, Any] = {"sources_used": []}
        session = httpx.Client(
            timeout=12.0,
            headers={
                "User-Agent": UA,
                "Referer": WISE_PARENT.format(symbol=symbol),
            },
            follow_redirects=True,
        )
        try:
            parent_html, encparam = self._get_encparam(session, symbol)
            if parent_html:
                ownership = parse_wisereport_ownership(parent_html)
                if ownership:
                    out["ownership"] = ownership
                    out["sources_used"].append("wisereport_c1010001")
            if not encparam:
                return out

            # cF3002 — annual statements.
            ann = self._wise_fetch(
                session,
                WISE_CF3002,
                params={
                    "cmp_cd": symbol,
                    "frq_typ": "Y",
                    "rpt_typ": "ISM",
                    "encparam": encparam,
                },
            )
            if isinstance(ann, dict):
                rows = parse_wisereport_cf3002(ann, "annual")
                if rows:
                    out["financials_annual"] = rows
                    out["sources_used"].append("wisereport_cf3002_annual")

            # cF3002 — quarterly statements.
            qtr = self._wise_fetch(
                session,
                WISE_CF3002,
                params={
                    "cmp_cd": symbol,
                    "frq_typ": "Q",
                    "rpt_typ": "ISM",
                    "encparam": encparam,
                },
            )
            if isinstance(qtr, dict):
                rows = parse_wisereport_cf3002(qtr, "quarterly")
                if rows:
                    out["financials_quarterly"] = rows
                    out["sources_used"].append("wisereport_cf3002_quarterly")

            # cF4002 — investment metrics.
            metrics = self._wise_fetch(
                session,
                WISE_CF4002,
                params={"cmp_cd": symbol, "frq_typ": "Y", "encparam": encparam},
            )
            if isinstance(metrics, dict):
                mrows = parse_wisereport_cf4002(metrics)
                if mrows:
                    out["financial_metrics"] = mrows
                    out["sources_used"].append("wisereport_cf4002")

            # cF9001 — sector / market comparison.
            sector = self._wise_fetch(
                session,
                WISE_CF9001,
                params={
                    "cmp_cd": symbol,
                    "data_typ": "1",
                    "sec_cd": "",
                    "chartType": "svg",
                },
            )
            if isinstance(sector, dict):
                sc = parse_wisereport_cf9001(sector)
                if sc:
                    out["sector_comparison"] = sc
                    out["sources_used"].append("wisereport_cf9001")

            return out
        finally:
            session.close()

    def _get_encparam(
        self, session: httpx.Client, symbol: str
    ) -> tuple[str | None, str | None]:
        """Hit the parent HTML page to obtain encparam + session cookies.

        Returns (html, encparam) — either may be None. The session keeps
        cookies internally so caller can immediately issue AJAX calls."""
        try:
            resp = session.get(WISE_PARENT.format(symbol=symbol), timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            logger.info("fnguide: wisereport parent fetch failed for %s: %s", symbol, exc)
            return None, None
        if resp.status_code != 200 or not resp.text:
            return None, None
        matches = ENCPARAM_RE.findall(resp.text)
        enc = matches[0] if matches else None
        return resp.text, enc

    def _wise_fetch(
        self,
        session: httpx.Client,
        url: str,
        params: dict[str, str],
    ) -> Any:
        """Issue an AJAX GET to WiseReport. Returns parsed JSON or None."""
        try:
            resp = session.get(url, params=params, timeout=12.0)
        except Exception as exc:  # noqa: BLE001
            logger.info("fnguide: wisereport GET %s failed: %s", url, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # --------- Legacy / optional ---------

    def _try_fnguide(self, symbol: str) -> dict[str, Any] | None:
        try:
            resp = self._request(
                "GET",
                FNGUIDE_URL.format(symbol=symbol),
                headers={
                    "User-Agent": UA,
                    "Referer": "https://comp.fnguide.com/",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=8.0,
                retries=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("fnguide: fnguide fetch failed for %s: %s", symbol, exc)
            return None
        if resp.status_code != 200:
            logger.info("fnguide: fnguide HTTP %s for %s", resp.status_code, symbol)
            return None
        try:
            return parse_fnguide_html(resp.text)
        except Exception:  # noqa: BLE001
            logger.exception("fnguide: fnguide parse failed for %s", symbol)
            return None

    def get_fchart_ohlcv(
        self, symbol: str, count: int = 1000, timeframe: str = "day"
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV bars from fchart.stock.naver.com. Not used by the REST
        endpoint by default — our Repository already holds OHLCV; this is here
        for completeness and for ad-hoc scripts."""
        try:
            resp = self._request(
                "GET",
                FCHART_URL,
                params={
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "count": str(count),
                    "requestType": "0",
                },
                headers={"User-Agent": UA, "Referer": "https://finance.naver.com/"},
                timeout=10.0,
                retries=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("fnguide: fchart fetch failed for %s: %s", symbol, exc)
            return []
        if resp.status_code != 200:
            return []
        try:
            return parse_fchart_xml(resp.text)
        except Exception:  # noqa: BLE001
            logger.exception("fnguide: fchart parse failed for %s", symbol)
            return []

    # --------- Shared helpers ---------

    def _fetch_json(
        self,
        url: str,
        *,
        referer: str,
        label: str,
        symbol: str,
    ) -> Any:
        """Issue a GET, return parsed JSON or None on any failure."""
        try:
            resp = self._request(
                "GET",
                url,
                headers={
                    "User-Agent": UA,
                    "Referer": referer,
                    "Accept": "application/json",
                },
                timeout=7.0,
                retries=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("fnguide: %s fetch failed for %s: %s", label, symbol, exc)
            return None
        if resp.status_code != 200:
            logger.info("fnguide: %s HTTP %s for %s", label, resp.status_code, symbol)
            return None
        try:
            return resp.json()
        except ValueError:
            logger.info("fnguide: %s non-json for %s", label, symbol)
            return None


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


_CORE_FIELDS = ("per", "pbr", "eps", "roe")


def _covers_core(d: dict[str, Any]) -> bool:
    return sum(1 for k in _CORE_FIELDS if d.get(k) is not None) >= 2


def _skeleton() -> dict[str, Any]:
    """Empty dict with the snapshot keys defined — used when no source replies."""
    return {
        "target_price": None,
        "investment_opinion": None,
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
        "peers": [],
        "summary_notes": [],
    }


def _merge(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = dict(primary)
    for k, v in fallback.items():
        if k in ("summary_notes", "peers", "financials_quarterly", "financials_annual"):
            current = out.get(k) or []
            if not current and v:
                out[k] = v
            continue
        if out.get(k) is None and v is not None:
            out[k] = v
    return out


def _prefer_richer(
    richer: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep NaverComp-style rows if they are non-empty, otherwise fallback.

    We also splice in any metrics from the fallback into matching periods so
    the UI can show both "revenue" (from Mobile) and "total_equity" (from
    NaverComp) on the same period record."""
    if not richer:
        return fallback
    by_period = {str(r.get("period", "")): dict(r) for r in richer}
    for fb in fallback or []:
        key = str(fb.get("period", ""))
        if not key:
            continue
        if key in by_period:
            for k, v in fb.items():
                if v is not None and by_period[key].get(k) is None:
                    by_period[key][k] = v
        else:
            by_period[key] = dict(fb)
    return sorted(by_period.values(), key=lambda r: str(r.get("period", "")), reverse=True)


def _payload_to_snapshot(
    payload: dict[str, Any],
    *,
    source: str,
    degraded: bool | None = None,
) -> FnguideSnapshot:
    """Rehydrate a stored FnguideSnapshot payload back into the dataclass.

    The stored payload is exactly ``FnguideSnapshot.to_dict()`` — so the
    constructor accepts the same keys. We override ``source`` so callers can
    distinguish ``db_fresh`` / ``db_stale`` from the original capture source.
    """
    symbol = payload.get("symbol") or ""
    return FnguideSnapshot(
        symbol=symbol,
        fetched_at=float(payload.get("fetched_at") or time.time()),
        source=source,
        degraded=bool(payload.get("degraded", False)) if degraded is None else bool(degraded),
        target_price=payload.get("target_price"),
        investment_opinion=payload.get("investment_opinion"),
        consensus_recomm_score=payload.get("consensus_recomm_score"),
        consensus_per=payload.get("consensus_per"),
        consensus_eps=payload.get("consensus_eps"),
        per=payload.get("per"),
        pbr=payload.get("pbr"),
        eps=payload.get("eps"),
        bps=payload.get("bps"),
        roe=payload.get("roe"),
        roa=payload.get("roa"),
        debt_ratio=payload.get("debt_ratio"),
        dividend_yield=payload.get("dividend_yield"),
        market_cap=payload.get("market_cap"),
        market_cap_raw=payload.get("market_cap_raw"),
        foreign_ratio=payload.get("foreign_ratio"),
        high_52w=payload.get("high_52w"),
        low_52w=payload.get("low_52w"),
        industry_code=payload.get("industry_code"),
        major_shareholder_name=payload.get("major_shareholder_name"),
        major_shareholder_pct=payload.get("major_shareholder_pct"),
        float_ratio=payload.get("float_ratio"),
        shares_outstanding=payload.get("shares_outstanding"),
        beta_52w=payload.get("beta_52w"),
        financials_quarterly=payload.get("financials_quarterly") or [],
        financials_annual=payload.get("financials_annual") or [],
        financial_metrics=payload.get("financial_metrics") or [],
        sector_comparison=payload.get("sector_comparison") or {},
        investor_trend=payload.get("investor_trend") or [],
        peers=payload.get("peers") or [],
        summary_notes=payload.get("summary_notes") or [],
        sources_used=payload.get("sources_used") or [],
    )


def _as_snapshot(
    symbol: str,
    data: dict[str, Any],
    *,
    source: str,
    degraded: bool = False,
    sources_used: list[str] | None = None,
) -> FnguideSnapshot:
    return FnguideSnapshot(
        symbol=symbol,
        fetched_at=time.time(),
        source=source,
        degraded=degraded,
        target_price=data.get("target_price"),
        investment_opinion=data.get("investment_opinion"),
        consensus_recomm_score=data.get("consensus_recomm_score"),
        consensus_per=data.get("consensus_per"),
        consensus_eps=data.get("consensus_eps"),
        per=data.get("per"),
        pbr=data.get("pbr"),
        eps=data.get("eps"),
        bps=data.get("bps"),
        roe=data.get("roe"),
        roa=data.get("roa"),
        debt_ratio=data.get("debt_ratio"),
        dividend_yield=data.get("dividend_yield"),
        market_cap=data.get("market_cap"),
        market_cap_raw=(
            str(data["market_cap_raw"]) if data.get("market_cap_raw") is not None else None
        ),
        foreign_ratio=data.get("foreign_ratio"),
        high_52w=data.get("high_52w"),
        low_52w=data.get("low_52w"),
        industry_code=data.get("industry_code"),
        major_shareholder_name=data.get("major_shareholder_name"),
        major_shareholder_pct=data.get("major_shareholder_pct"),
        float_ratio=data.get("float_ratio"),
        shares_outstanding=data.get("shares_outstanding"),
        beta_52w=data.get("beta_52w"),
        financials_quarterly=data.get("financials_quarterly") or [],
        financials_annual=data.get("financials_annual") or [],
        financial_metrics=data.get("financial_metrics") or [],
        sector_comparison=data.get("sector_comparison") or {},
        investor_trend=data.get("investor_trend") or [],
        peers=data.get("peers") or [],
        summary_notes=data.get("summary_notes") or [],
        sources_used=sources_used or [],
    )
