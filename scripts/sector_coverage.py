"""WICS-34 섹터 매트릭스의 셀 단위 커버리지 분석.

Parses ``docs/SECTOR_INDICATOR_MAP.md`` to extract every (sector, indicator,
source) tuple, then checks each tuple against the actual data on disk
(``~/.gazua/data/sectors/``) to report cell-level coverage.

Output:
  - Per-sector table:  sector | filled | total | %
  - Per-source table:  source | cells_referenced | cells_filled
  - Saves ``data/sector_coverage.json`` for downstream tracking.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = REPO / "docs" / "SECTOR_INDICATOR_MAP.md"
DATA_ROOT = Path.home() / ".gazua" / "data" / "sectors"
OUT_PATH = REPO / "data" / "sector_coverage.json"

# Source token → list of disk-folder names that satisfy that token.
# Each cell's source string can mention multiple tokens; we satisfy the cell
# if ANY token resolves to a non-empty disk folder.
SOURCE_RESOLVE = {
    "KIS":      ["kis", "kis_index"],
    "KOSIS":    ["kosis", "imported_kosis"],
    "ECOS":     ["ecos", "imported_ecos"],
    "FRED":     ["fred", "imported_fred"],
    "DART":     ["dart"],
    "EIA":      ["eia"],            # not collected yet (adapter exists)
    "EXIM":     ["exim"],
    "Naver":    ["naver"],
    "TRASS":    ["customs"],
    "OECD":     ["oecd"],
    "WB":       ["worldbank"],
    "IMF":      ["imf"],
    "Comtrade": ["un_comtrade"],
    "pytrends": ["pytrends"],
    "CFTC":     ["cftc"],
    "Crawl":    ["scrapers", "bdi", "scfi", "yf_commodities", "openfda", "stooq"],
    "yfinance": ["yfinance", "yf_commodities"],
    "Polygon":  ["polygon"],
}


def _parse_map() -> list[dict]:
    """Walk the markdown file and return one record per cell.
    Sector boundary detected by ``## [NN] sector_name`` headings."""
    text = MAP_PATH.read_text(encoding="utf-8")
    cells: list[dict] = []
    sector = None
    sector_idx = None

    sector_re = re.compile(r"^##\s*\[(\d{2})\]\s+(.+?)\s*$", re.M)
    # Find each sector chunk, then walk its rows.
    matches = list(sector_re.finditer(text))
    for i, m in enumerate(matches):
        sec_num = m.group(1)
        sec_name = m.group(2).strip()
        chunk_start = m.end()
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[chunk_start:chunk_end]
        # Indicator rows look like: "| 1 | … | source | category | lead |"
        for row in re.findall(
            r"^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
            chunk,
            re.M,
        ):
            idx, indicator, source_raw, category, leadlag = row
            cells.append(
                {
                    "sector_num": sec_num,
                    "sector_name": sec_name,
                    "cell_num": int(idx),
                    "indicator": indicator.strip(),
                    "source_raw": source_raw.strip(),
                    "category": category.strip(),
                    "leadlag": leadlag.strip(),
                }
            )
    return cells


def _disk_inventory() -> dict[str, int]:
    """Return {disk_folder: file_count} for non-empty folders under DATA_ROOT."""
    out: dict[str, int] = {}
    if not DATA_ROOT.is_dir():
        return out
    for child in DATA_ROOT.iterdir():
        if child.is_dir() and not child.name.startswith("_"):
            if child.name == "imported":
                for sub in child.iterdir():
                    if sub.is_dir():
                        n = len(list(sub.glob("*.csv")))
                        if n > 0:
                            out[f"imported_{sub.name}"] = n
            else:
                n = len(list(child.glob("*.csv")))
                if n > 0:
                    out[child.name] = n
    return out


def _classify_cell(cell: dict, disk: dict[str, int]) -> dict:
    """Mark a cell as filled if any token in its source string maps to a disk
    folder with at least 1 CSV."""
    src = cell["source_raw"]
    tokens_present = []
    tokens_filled = []
    for token, folders in SOURCE_RESOLVE.items():
        if token in src:
            tokens_present.append(token)
            if any(disk.get(f, 0) > 0 for f in folders):
                tokens_filled.append(token)
    cell["tokens_in_source"] = tokens_present
    cell["tokens_filled"] = tokens_filled
    cell["filled"] = len(tokens_filled) > 0
    return cell


def main() -> int:
    if not MAP_PATH.exists():
        print(f"map file missing: {MAP_PATH}")
        return 1
    cells = _parse_map()
    disk = _disk_inventory()
    if not cells:
        print("no cells parsed from map")
        return 1
    cells = [_classify_cell(c, disk) for c in cells]

    # Per-sector summary
    by_sec: dict[str, dict] = defaultdict(lambda: {"total": 0, "filled": 0, "name": ""})
    for c in cells:
        key = c["sector_num"]
        by_sec[key]["total"] += 1
        by_sec[key]["filled"] += int(c["filled"])
        by_sec[key]["name"] = c["sector_name"]

    # Per-source summary
    by_src: dict[str, dict] = defaultdict(lambda: {"referenced": 0, "filled": 0})
    for c in cells:
        for t in c["tokens_in_source"]:
            by_src[t]["referenced"] += 1
            if t in c["tokens_filled"]:
                by_src[t]["filled"] += 1

    total_cells = len(cells)
    total_filled = sum(1 for c in cells if c["filled"])
    pct = 100 * total_filled / total_cells if total_cells else 0

    print(f"\n{'=' * 64}")
    print(f"WICS-34 SECTOR COVERAGE  ({total_filled}/{total_cells} cells filled = {pct:.1f}%)")
    print("=" * 64)
    print(f"{'#':>3}  {'sector':25} {'filled':>6} {'/total':>7} {'%':>5}")
    print("-" * 64)
    for key in sorted(by_sec.keys()):
        v = by_sec[key]
        ratio = f"{100 * v['filled'] / v['total']:.0f}%" if v["total"] else "-"
        print(f"{key:>3}  {v['name'][:25]:25} {v['filled']:>6} {v['total']:>7} {ratio:>5}")
    print()

    print(f"\n{'=' * 64}")
    print(f"BY SOURCE  (referenced / has-data on disk)")
    print("=" * 64)
    print(f"{'source':12} {'cells_ref':>10} {'cells_filled':>14}")
    print("-" * 64)
    for token in sorted(by_src.keys()):
        v = by_src[token]
        print(f"{token:12} {v['referenced']:>10} {v['filled']:>14}")
    print()

    print("Disk folders snapshot:")
    for folder, count in sorted(disk.items()):
        print(f"  {folder:20} {count:>4} files")

    OUT_PATH.write_text(
        json.dumps(
            {
                "total_cells": total_cells,
                "total_filled": total_filled,
                "pct_filled": round(pct, 1),
                "per_sector": dict(by_sec),
                "per_source": dict(by_src),
                "disk_inventory": disk,
                "cells": cells,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\ndetail → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
