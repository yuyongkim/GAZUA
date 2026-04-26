"""DART 사업부문별 매출 (segment revenue) extractor.

OpenDART exposes the full business report text via the ``document.xml``
endpoint — a ZIP archive containing the filing's XBRL + HTML. The segment
breakdown lives inside the HTML under section II. 사업의 내용 →
"사업부문별 매출" / "주요 제품 및 서비스" tables. There is no structured
JSON endpoint for this data, so we have to scrape.

Parsing strategy (multi-stage, best-effort):

1. For a given ``corp_code``, fetch the latest annual 사업보고서 receipt via
   ``list.json`` (pblntf_detail_ty=A001).
2. ``GET /api/document.xml?rcept_no=...`` returns ``application/octet-stream``
   (a ZIP). Unzip in-memory.
3. Pick HTML-ish members from the archive (the body + any annex sections).
4. **Section scoping** — detect DART-style headings (``<TITLE>`` with
   ATOC=Y, ``<cover-title>``, or h1..h4 whose text matches "사업부문별
   매출" / "주요 제품 및 서비스" / "영업부문 정보" / "II. 사업의 내용") and
   collect the tables that follow each heading. This is the primary upgrade
   over the previous whole-document scan that silently matched subsidiary
   footnote lists (Gauss Labs, SK hynix NAND, ... row=-1,-2,-3). Ported
   from ``F:/SEC/License/sec-license-extraction/parser``.
5. Within each scoped block, score tables and pick the best. Hard rejects:
   subsidiaries lists (회사명/소재지/지분율 columns + ``(*1)`` row markers),
   chart-of-accounts notes (미수수익/매출채권/미수금 + fiscal-year labels),
   and "row-index" pathology (values are -1..-N sequential).
6. Emit: ``{symbol, period_end, segment_name, revenue, operating_income,
   revenue_share, source_id='dart'}``.

Every stage can fail; the caller should treat the result as ``Optional``
and fall back to the PBR proxy in :mod:`ky_core.value.segment`.
"""
from __future__ import annotations

import io
import logging
import re
import warnings
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter
from ky_adapters.dart.client import DART_BASE, REPORT_CODES, _yyyymmdd

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data model                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class Segment:
    symbol: str
    corp_code: str
    period_end: str           # ISO YYYY-12-31
    segment_name: str
    revenue: float | None = None
    operating_income: float | None = None
    revenue_share: float | None = None  # 0..1
    source_id: str = "dart"

    def as_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "corp_code": self.corp_code,
            "period_end": self.period_end,
            "period_type": "FY",
            "segment_name": self.segment_name,
            "revenue": self.revenue,
            "operating_income": self.operating_income,
            "revenue_share": self.revenue_share,
            "source_id": self.source_id,
        }


# --------------------------------------------------------------------------- #
# Segment-table heuristic vocabulary                                          #
# --------------------------------------------------------------------------- #

# Korean-number parser: handles comma separators, negative signs, "△" used on
# Korean filings for negative numbers, and trailing units we ignore.
_NUM_RE = re.compile(r"[+\-−△▽]?\d[\d,]*(?:\.\d+)?")

# NB: DART cells often have internal whitespace ("구  분"/"매  출  액"). All
# token matches below run on the *whitespace-compacted* text to handle that.
_KOR_SEG_HEADERS = ("사업부문", "부문명", "부문구분", "영업부문", "부문", "구분")
_KOR_REV_HEADERS = ("매출", "수익", "매출액", "외부매출", "순매출", "금액")
_KOR_PCT_HEADERS = ("비중", "구성비", "비율", "점유율")
_KOR_OPINCOME_HEADERS = ("영업이익", "영업손익")

# Tokens commonly appearing in the *first column* of a real segment table
# when the header row is generic. Purposely narrow — "제품"/"상품"/"용역"
# overlap with chart-of-accounts rows and were excluded.
_SEG_ROW_PREFIXES = (
    "부문", "사업부",
    "국내", "해외",
    "완성차", "자동차",
    "메모리", "반도체", "디스플레이", "모바일", "가전",
    "화학", "정유", "석유", "건설", "조선",
    "금융업", "보험업", "증권업", "카드업", "은행업",
    "바이오", "제약", "통신",
    "DX", "DS", "SDC", "Harman",     # Samsung-specific
    "IT", "R&D",
)

# Section-heading pattern vocabulary. Earlier = higher priority.
_SECTION_HEADING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("seg_sales_primary", re.compile(r"(?:사업)?부문별\s*매출\s*(?:현황|실적)?", re.I)),
    ("main_products",     re.compile(r"주요\s*제품\s*(?:및|and)?\s*서비스", re.I)),
    ("seg_info_notes",    re.compile(r"(?:영업|사업)?부문\s*(?:별)?\s*정보", re.I)),
    ("biz_overview",      re.compile(r"[IⅡII]{1,3}\.?\s*사업의\s*내용", re.I)),
    ("segment_en",        re.compile(r"\bsegment\s+(?:information|revenue|sales)\b", re.I)),
)

# Subsidiaries-list rejection. Previously matched on SK hynix producing
# Gauss Labs Inc.(*1) | revenue=-1 bogus rows.
_SUBSIDIARY_HEADER_TOKENS = (
    "회사명", "종속회사", "종속기업", "자회사", "관계회사",
    "소재지", "국가", "주요사업", "지분율", "소유지분",
    "결산월", "업종", "설립일", "업종분류",
    "company", "subsidiary", "country", "jurisdiction",
)
_FOOTNOTE_ROW_RE = re.compile(r"\(\s*\*\s*\d+\s*\)")       # (*1) / (* 2 )
_SUPERSCRIPT_ROW_RE = re.compile(r"^\s*\*\s*\d+\s*[\).]?") # leading *1) or *1.

# Chart-of-accounts / financial-note rejection. Hyundai 2026-03 pathology
# matched a 매출채권 breakdown (미수수익/미수금/선급금/...) as if it were
# a segment table because the header said "매출".
_ACCOUNTING_LINE_TOKENS = (
    "미수수익", "미수금", "선급금", "선급비용", "미청구공사",
    "대손충당금", "대손상각", "매출채권", "기타채권",
    "선수수익", "선수금", "미지급", "미지급금",
    "재고자산", "유형자산", "무형자산", "금융자산", "금융부채",
    "예수금", "충당부채", "퇴직급여",
)
_FISCAL_YEAR_LABEL_RE = re.compile(r"^제\s*\d+\s*기")   # "제58기", "제 57 기"
_YEAR_COL_RE = re.compile(r"(?:20\d{2})\s*년|제\s*\d+\s*기")  # "2025년", "제58기"


# --------------------------------------------------------------------------- #
# Small utilities                                                             #
# --------------------------------------------------------------------------- #


def _compact(s: str) -> str:
    """Strip all whitespace from ``s`` for Korean keyword matching."""
    return re.sub(r"\s+", "", s or "")


def _parse_num(text: str) -> float | None:
    if not text:
        return None
    t = text.strip().replace(",", "")
    neg = False
    if t.startswith(("△", "▽", "(")) or t.endswith(")"):
        neg = True
        t = t.strip("()△▽ ")
    match = _NUM_RE.search(t)
    if not match:
        return None
    try:
        v = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    if neg:
        v = -v
    return v


# --------------------------------------------------------------------------- #
# Table-level classifiers                                                     #
# --------------------------------------------------------------------------- #


def _looks_like_segment_table(
    headers: list[str],
    first_col_values: list[str] | None = None,
) -> bool:
    """Does this table's header row (optionally cross-checked with the
    first-column values) look like a real segment-revenue table?

    Two acceptance paths:

    * **Strict header path** — header contains an explicit segment vocabulary
      token (사업부문 / 부문명 / 영업부문) AND a revenue token.
    * **Generic header path** — header is generic ("구분 / 주요 제품") but
      ≥2 first-column values carry a segment-ish prefix (부문 / 국내 /
      메모리 / 화학 / DX / ...). This is the Samsung "주요 제품 및 서비스"
      pattern — the header says nothing about "부문", but first column is
      DX 부문 / DS 부문 / SDC / Harman / 기타.
    """
    hcompact = _compact(" ".join(headers))
    has_rev_header = any(tok in hcompact for tok in _KOR_REV_HEADERS)
    has_year_col = any(_YEAR_COL_RE.search(h) for h in headers)

    strict_tokens = ("사업부문", "부문명", "부문구분", "영업부문")
    has_strict_seg = any(tok in hcompact for tok in strict_tokens)

    # Time-series segment table: header has 사업부문 + year columns (2025년 /
    # 제58기) but no explicit 매출. Very common in 사업보고서 Item 2.
    if has_strict_seg and has_year_col:
        return True
    if not has_rev_header:
        return False

    if has_strict_seg:
        return True

    if not any(tok in hcompact for tok in ("부문", "구분")):
        return False
    if not first_col_values:
        return "부문" in hcompact

    def _row_has_prefix(v: str) -> bool:
        vc = _compact(v)
        return any(tok in vc for tok in _SEG_ROW_PREFIXES)

    return sum(1 for v in first_col_values if _row_has_prefix(v)) >= 2


def _looks_like_subsidiary_table(
    headers: list[str],
    first_col_values: list[str],
) -> bool:
    """True if the table is a subsidiaries / affiliates list."""
    hcompact = _compact(" ".join(headers))
    header_hits = sum(1 for tok in _SUBSIDIARY_HEADER_TOKENS if tok in hcompact)
    if header_hits >= 2:
        return True
    if not first_col_values:
        return False
    footnote_rows = sum(
        1 for v in first_col_values
        if _FOOTNOTE_ROW_RE.search(v) or _SUPERSCRIPT_ROW_RE.search(v)
    )
    return footnote_rows / max(len(first_col_values), 1) > 0.6


def _looks_like_accounting_notes_table(first_col_values: list[str]) -> bool:
    """True if the rows are chart-of-accounts line items or fiscal-year
    labels. Triggered on the Hyundai 2026-03 receivables breakdown that
    had ``매출`` in header, but whose rows were 미수수익/미수금/선급금/...
    """
    if not first_col_values:
        return False
    acct_hits = sum(
        1 for v in first_col_values
        if any(tok in v for tok in _ACCOUNTING_LINE_TOKENS)
    )
    fy_hits = sum(1 for v in first_col_values if _FISCAL_YEAR_LABEL_RE.match(v.strip()))
    if acct_hits >= 2:
        return True
    if fy_hits >= 1 and acct_hits >= 1:
        return True
    total_only = sum(
        1 for v in first_col_values
        if _compact(v) in ("합계", "총계", "계", "소계")
    )
    if total_only >= 2:
        return True
    return False


def _looks_like_row_index_values(values: list[Any]) -> bool:
    """SK hynix pathology detector — revenue column is -1, -2, -3, ... -N."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if len(nums) < 3:
        return False
    if not all(abs(v) < 100 for v in nums):
        return False
    sorted_abs = sorted(int(abs(v)) for v in nums)
    if sorted_abs == list(range(1, len(sorted_abs) + 1)):
        return True
    if len(set(nums)) == len(nums) and max(nums) - min(nums) == len(nums) - 1:
        return True
    return False


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #


class DARTSegmentExtractor(BaseAdapter):
    """Fetches & parses segment revenue breakdowns from DART filings.

    Instantiate via ``DARTSegmentExtractor.from_settings()`` — reuses the same
    ``DART_API_KEY`` that :class:`DARTAdapter` consumes.
    """

    source_id = "dart_segments"
    priority = 10

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DART_BASE,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "DARTSegmentExtractor":
        return cls(api_key=cls._env("DART_API_KEY"))

    def healthcheck(self) -> dict[str, Any]:
        if not self.api_key:
            return self._timed_fail(self.source_id, "DART_API_KEY not configured")
        return self._timed_ok(0.0, self.source_id, {"configured": True})

    # ---------- API surface ----------

    def find_latest_annual_receipt(
        self,
        corp_code: str,
        year: int | None = None,
    ) -> Optional[tuple[str, str]]:
        """Return ``(receipt_no, period_end_iso)`` for the latest 사업보고서."""
        if not self.api_key:
            raise AuthError("DART_API_KEY not configured")
        target_year = year or (date.today().year - 1)
        for offset in range(0, 3):
            y = target_year - offset
            bgn = f"{y}0101"
            end = f"{y + 1}0601"
            try:
                resp = self._request(
                    "GET",
                    f"{self.base_url}/list.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn,
                        "end_de": end,
                        "pblntf_detail_ty": "A001",
                        "page_count": 20,
                        "page_no": 1,
                    },
                )
            except AdapterError as exc:
                log.warning("dart list failed for %s: %s", corp_code, exc)
                continue
            if resp.status_code != 200:
                continue
            body = resp.json()
            if body.get("status") != "000":
                continue
            for row in body.get("list", []):
                name = str(row.get("report_nm", ""))
                if "사업보고서" in name and "기재정정" not in name:
                    return (row.get("rcept_no", ""), f"{y}-12-31")
        return None

    def fetch_document_archive(self, receipt_no: str) -> bytes:
        """Download a filing archive. Returns raw ZIP bytes."""
        if not self.api_key:
            raise AuthError("DART_API_KEY not configured")
        resp = self._request(
            "GET",
            f"{self.base_url}/document.xml",
            params={"crtfc_key": self.api_key, "rcept_no": receipt_no},
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise AdapterError(f"DART document → HTTP {resp.status_code}")
        return resp.content

    def extract_segments(
        self,
        symbol: str,
        corp_code: str,
        year: int | None = None,
    ) -> list[Segment]:
        """End-to-end: list → document → parse. Empty list on any failure."""
        try:
            rcept = self.find_latest_annual_receipt(corp_code, year=year)
        except Exception as exc:  # noqa: BLE001
            log.warning("segment receipt lookup failed for %s: %s", symbol, exc)
            return []
        if not rcept:
            return []
        receipt_no, period_end = rcept

        try:
            archive = self.fetch_document_archive(receipt_no)
        except Exception as exc:  # noqa: BLE001
            log.warning("segment archive fetch failed for %s: %s", symbol, exc)
            return []

        docs = _unzip_filing(archive)
        if not docs:
            return []

        for raw in docs:
            try:
                rows = _parse_segment_tables(raw)
            except Exception as exc:  # noqa: BLE001
                log.debug("parse attempt failed for %s: %s", symbol, exc)
                continue
            if rows:
                return [
                    Segment(
                        symbol=symbol,
                        corp_code=corp_code,
                        period_end=period_end,
                        segment_name=r["name"],
                        revenue=r.get("revenue"),
                        operating_income=r.get("operating_income"),
                        revenue_share=r.get("revenue_share"),
                    )
                    for r in rows
                ]
        return []


# --------------------------------------------------------------------------- #
# Parsing pipeline                                                            #
# --------------------------------------------------------------------------- #


def _unzip_filing(archive: bytes) -> list[bytes]:
    """Return filing body bytes. DART's document.xml response is a ZIP."""
    if not archive:
        return []
    if archive[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile:
            return []
        out: list[bytes] = []
        for name in zf.namelist():
            if name.lower().endswith((".xml", ".html", ".htm", ".xhtml")):
                try:
                    out.append(zf.read(name))
                except Exception:  # noqa: BLE001
                    continue
        return out
    return [archive]


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _parse_segment_tables(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Multi-stage parse: BS4 section-scoped → BS4 whole-doc → regex."""
    text = _decode(raw_bytes)
    rows = _parse_with_bs4(text)
    if rows:
        return rows
    return _parse_with_regex(text)


def _parse_with_bs4(text: str) -> list[dict[str, Any]]:
    try:
        # Suppress lxml's XMLParsedAsHTMLWarning — DART filings are malformed
        # XML and we deliberately parse them with the HTML parser.
        from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(text, "lxml")
    except Exception:  # noqa: BLE001
        try:
            soup = BeautifulSoup(text, "html.parser")
        except Exception:  # noqa: BLE001
            return []

    # Stage 1: section-scoped search.
    scoped = _tables_under_segment_headings(soup)
    rows = _select_best_segment_table(scoped)
    if rows:
        return rows

    # Stage 2: whole-document fallback with the same rejection chain.
    return _select_best_segment_table(soup.find_all("table"))


# --------------------------------------------------------------------------- #
# Section scoping (ported from F:/SEC parser heuristics)                      #
# --------------------------------------------------------------------------- #


def _tables_under_segment_headings(soup) -> list:
    """Collect tables that follow a heading whose text matches a segment
    section pattern. DART documents use <TITLE>, <cover-title>, h1..h4, and
    often <SPAN USERMARK> as heading markers — we scan those tag kinds.
    """
    allowed_tags = (
        "title", "cover-title",
        "h1", "h2", "h3", "h4",
        "p", "span", "b", "strong", "div",
    )
    candidates: list[tuple[int, Any]] = []  # (priority, heading_tag)
    for tag in soup.find_all(allowed_tags):
        text = " ".join(tag.stripped_strings)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 3 or len(text) > 180:
            continue
        for priority, (_key, pat) in enumerate(_SECTION_HEADING_PATTERNS):
            if pat.search(text):
                candidates.append((priority, tag))
                break

    if not candidates:
        return []

    candidates.sort(key=lambda x: x[0])

    out = []
    seen_ids: set[int] = set()
    for _priority, heading_tag in candidates:
        for tbl in _tables_after_heading(heading_tag, max_lookahead=60):
            if id(tbl) not in seen_ids:
                seen_ids.add(id(tbl))
                out.append(tbl)
    return out


def _tables_after_heading(heading_tag, *, max_lookahead: int = 60) -> list:
    """Walk ``heading_tag.find_all_next`` in document order; collect <table>
    nodes until the next strong heading boundary.
    """
    out = []
    count = 0
    for nxt in heading_tag.find_all_next(True):
        if count >= max_lookahead:
            break
        name = nxt.name.lower() if nxt.name else ""
        if name == "table":
            out.append(nxt)
            count += 1
            continue
        if name in ("cover-title",) or (
            name == "title" and (nxt.get("ATOC") or nxt.get("atoc") or "").upper() == "Y"
        ):
            if nxt is not heading_tag:
                break
        if name in ("h1", "h2", "h3"):
            head_text = " ".join(nxt.stripped_strings)[:80]
            if re.match(r"^\s*(?:[IVXLC]+\.|\d+\.)", head_text):
                break
    return out


# --------------------------------------------------------------------------- #
# Table scoring & extraction                                                  #
# --------------------------------------------------------------------------- #


def _select_best_segment_table(tables: list) -> list[dict[str, Any]]:
    """Pick the highest-scoring table-parse across candidates."""
    best_rows: list[dict[str, Any]] = []
    best_score = -1
    for tbl in tables:
        rows, score = _try_extract_segment_rows(tbl)
        if not rows:
            continue
        if score > best_score:
            best_rows, best_score = rows, score
    return best_rows


def _try_extract_segment_rows(tbl) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, quality_score). Empty list + score=-1 on any reject."""
    try:
        rows = tbl.find_all("tr")
    except Exception:  # noqa: BLE001
        return [], -1
    if len(rows) < 2:
        return [], -1

    header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    first_col_values: list[str] = []
    for body_row in rows[1:]:
        cells = body_row.find_all(["td", "th"])
        if cells:
            first_col_values.append(cells[0].get_text(" ", strip=True))

    if not _looks_like_segment_table(header_cells, first_col_values):
        return [], -1
    if _looks_like_subsidiary_table(header_cells, first_col_values):
        return [], -1
    if _looks_like_accounting_notes_table(first_col_values):
        return [], -1

    col_name = _match_col(header_cells, _KOR_SEG_HEADERS)
    col_rev = _match_col(header_cells, _KOR_REV_HEADERS, _KOR_OPINCOME_HEADERS)
    col_pct = _match_col(header_cells, _KOR_PCT_HEADERS)
    col_opinc = _match_col(header_cells, _KOR_OPINCOME_HEADERS)
    # Time-series fallback: if there's no 매출 header, pick the leftmost
    # year-column as the revenue source. Years are typically sorted newest→
    # oldest in Korean filings (2025 / 2024 / 2023), so the leftmost year
    # column is the most recent period.
    if col_rev is None:
        for i, h in enumerate(header_cells):
            if _YEAR_COL_RE.search(h):
                col_rev = i
                break
    if col_name is None or col_rev is None:
        return [], -1

    parsed: list[dict[str, Any]] = []
    for body_row in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in body_row.find_all(["td", "th"])]
        if not cells or col_name >= len(cells):
            continue
        name = cells[col_name]
        if not name:
            continue
        cname = _compact(name)
        if cname.startswith(("합계", "총계", "계", "소계")):
            continue
        if re.fullmatch(r"[\d,\.\- ]+", name):
            continue
        if _FOOTNOTE_ROW_RE.search(name) or _SUPERSCRIPT_ROW_RE.search(name):
            continue
        if _FISCAL_YEAR_LABEL_RE.match(name.strip()):
            continue
        if any(tok in name for tok in _ACCOUNTING_LINE_TOKENS):
            continue

        revenue = _parse_num(cells[col_rev]) if col_rev < len(cells) else None
        pct = _parse_num(cells[col_pct]) if (col_pct is not None and col_pct < len(cells)) else None
        op_income = (
            _parse_num(cells[col_opinc])
            if (col_opinc is not None and col_opinc != col_rev and col_opinc < len(cells))
            else None
        )
        share = None
        if pct is not None:
            # "47.2" → 0.472 ; "0.47" → 0.47 (already in [0,1])
            candidate = pct / 100.0 if abs(pct) > 1.5 else pct
            # Sanity clamp — shares larger than 2.0 in absolute value are
            # almost certainly a revenue number misread into the % column
            # (e.g. the Hyundai accounting table had 29.5% alongside 219.0%
            # because two rows mixed 구성비 / 성장률 columns).
            if -2.0 <= candidate <= 2.0:
                share = candidate
        parsed.append(
            {
                "name": name,
                "revenue": revenue,
                "operating_income": op_income,
                "revenue_share": share,
            }
        )

    usable = [r for r in parsed if r.get("revenue") or r.get("revenue_share")]
    if len(usable) < 2:
        return [], -1

    # Row-index pathology (SK hynix -1,-2,-3...).
    if _looks_like_row_index_values([r.get("revenue") for r in usable]):
        return [], -1

    # Drop absurdly-tiny outliers.
    rev_abs = [abs(v) for v in (r.get("revenue") for r in usable) if isinstance(v, (int, float)) and v]
    if rev_abs:
        maxr, minr = max(rev_abs), min(rev_abs)
        if minr > 0 and maxr / minr > 1e6:
            usable = [
                r for r in usable
                if r.get("revenue") is None or abs(r["revenue"]) > maxr / 1e5
            ]
        if len(usable) < 2:
            return [], -1

    # --- Scoring ---
    score = 0
    score += len(usable) * 2
    if col_pct is not None and any(r.get("revenue_share") for r in usable):
        score += 6
    if col_opinc is not None and any(r.get("operating_income") for r in usable):
        score += 3
    if 2 <= len(usable) <= 12:
        score += 4
    elif len(usable) > 20:
        score -= 6  # probably a subsidiary list that slipped through
    hcompact = _compact(" ".join(header_cells))
    if "사업부문" in hcompact or "부문명" in hcompact:
        score += 2
    if "비중" in hcompact:
        score += 1

    return usable, score


def _match_col(
    headers: list[str],
    want: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> int | None:
    """Find the first header index whose compacted text contains any ``want``
    token and no ``exclude`` token.
    """
    for i, h in enumerate(headers):
        hc = _compact(h)
        if any(tok in hc for tok in exclude):
            continue
        if any(tok in hc for tok in want):
            return i
    return None


# --------------------------------------------------------------------------- #
# Regex fallback — for pure-XML reports                                       #
# --------------------------------------------------------------------------- #


def _parse_with_regex(text: str) -> list[dict[str, Any]]:
    """Very loose regex fallback. Returns rows only when we're confident."""
    block_re = re.compile(
        r"(?:사업)?부문별[^<]{0,80}매출[\s\S]{0,4000}?</table>",
        re.IGNORECASE,
    )
    m = block_re.search(text)
    if not m:
        return []
    block = m.group(0)
    block = re.sub(r"</tr>", "\n", block, flags=re.IGNORECASE)
    block = re.sub(r"</td>|</th>", "\t", block, flags=re.IGNORECASE)
    block = re.sub(r"<[^>]+>", "", block)
    out: list[dict[str, Any]] = []
    for line in block.splitlines():
        cells = [c.strip() for c in line.split("\t") if c.strip()]
        if len(cells) < 2:
            continue
        name = cells[0]
        if re.fullmatch(r"[\d,\.\- %]+", name):
            continue
        cname = _compact(name)
        if cname.startswith(("합계", "총계", "계", "소계", "사업부문", "부문명")):
            continue
        if _FOOTNOTE_ROW_RE.search(name) or _SUPERSCRIPT_ROW_RE.search(name):
            continue
        if _FISCAL_YEAR_LABEL_RE.match(name.strip()):
            continue
        if any(tok in name for tok in _ACCOUNTING_LINE_TOKENS):
            continue
        revenue = _parse_num(cells[1]) if len(cells) > 1 else None
        pct = None
        for c in cells[2:]:
            if "%" in c or "." in c:
                pct = _parse_num(c)
                if pct is not None:
                    break
        share = (pct / 100.0) if (pct is not None and pct > 1.5) else pct
        if revenue is None and share is None:
            continue
        out.append(
            {
                "name": name,
                "revenue": revenue,
                "operating_income": None,
                "revenue_share": share,
            }
        )
    if _looks_like_row_index_values([r["revenue"] for r in out]):
        return []
    return out[:12]


# Re-export unused constants to silence "imported but unused" warnings when
# downstream modules import from this module.
_ = (REPORT_CODES, _yyyymmdd)
