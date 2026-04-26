"""Sector indicator collector.

Iterates ``ky_core.sectors.all_specs()``, calls the matching adapter for each
spec, and saves results to ``~/.gazua/data/sectors/<source>/<slug>.csv``.
A run-level manifest is written to ``~/.gazua/data/sectors/_manifest.json``
so subsequent jobs can incrementally refresh stale series.

Usage:
    python scripts/collect_sectors.py                  # full sweep
    python scripts/collect_sectors.py --source customs # one source only
    python scripts/collect_sectors.py --dry-run        # plan, no I/O
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Force UTF-8 stdout on Windows cp949 consoles so Korean labels print cleanly.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# Make the workspace packages importable when running from repo root
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "packages" / "core"))
sys.path.insert(0, str(_REPO / "packages" / "adapters"))

from ky_core.sectors import IndicatorSpec, all_specs, specs_by_source  # noqa: E402

logger = logging.getLogger("collect_sectors")

OUT_ROOT = Path.home() / ".gazua" / "data" / "sectors"
MANIFEST_PATH = OUT_ROOT / "_manifest.json"


# ----- Adapter wiring -----------------------------------------------------

def _load_adapter(source: str):
    """Lazily import + instantiate the matching adapter."""
    if source == "customs":
        from ky_adapters.customs import CustomsAdapter
        return CustomsAdapter.from_settings()
    if source == "fred":
        from ky_adapters.fred import FREDAdapter
        return FREDAdapter.from_settings()
    if source == "ecos":
        from ky_adapters.ecos.client import ECOSAdapter
        return ECOSAdapter.from_settings()
    if source == "kosis":
        from ky_adapters.kosis.client import KOSISAdapter
        return KOSISAdapter.from_settings()
    if source == "dart":
        from ky_adapters.dart.client import DARTAdapter
        return DARTAdapter.from_settings()
    if source == "kis_index":
        from ky_adapters.kis.market import KISMarketAdapter
        return KISMarketAdapter.from_settings()
    if source == "yf_commodities":
        from ky_adapters.scrapers import YFinanceCommoditiesAdapter
        return YFinanceCommoditiesAdapter.from_settings()
    if source == "openfda":
        from ky_adapters.scrapers import OpenFDAAdapter
        return OpenFDAAdapter.from_settings()
    if source == "stooq":
        from ky_adapters.scrapers import StooqQuoteAdapter
        return StooqQuoteAdapter.from_settings()
    if source == "eia":
        from ky_adapters.eia import EIAAdapter
        return EIAAdapter.from_settings()
    if source == "oecd":
        from ky_adapters.oecd import OECDAdapter
        return OECDAdapter.from_settings()
    if source == "worldbank":
        from ky_adapters.worldbank import WorldBankAdapter
        return WorldBankAdapter.from_settings()
    if source == "pytrends":
        from ky_adapters.pytrends import PyTrendsAdapter
        return PyTrendsAdapter.from_settings()
    if source == "cftc":
        from ky_adapters.cftc import CFTCAdapter
        return CFTCAdapter.from_settings()
    if source == "un_comtrade":
        from ky_adapters.un_comtrade import UNComtradeAdapter
        return UNComtradeAdapter.from_settings()
    raise ValueError(f"unknown source: {source}")


# ----- Persistence --------------------------------------------------------

def _row_dict(obj: Any) -> dict[str, Any]:
    """Coerce an arbitrary observation/dataclass/dict into a flat dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "as_row"):
        try:
            return obj.as_row()
        except TypeError:
            pass
    if is_dataclass(obj):
        return asdict(obj)
    return {"value": str(obj)}


def _write_csv(rows: list[dict[str, Any]], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        target.write_text("", encoding="utf-8")
        return
    # Union of keys for header — keep order: first row keys, then any new keys
    seen = list(rows[0].keys())
    for r in rows[1:]:
        for k in r.keys():
            if k not in seen:
                seen.append(k)
    with target.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=seen)
        w.writeheader()
        for r in rows:
            w.writerow({k: _to_csv(v) for k, v in r.items()})


def _to_csv(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


# ----- Per-spec collection ------------------------------------------------

def collect_one(adapter: Any, spec: IndicatorSpec) -> dict[str, Any]:
    method = getattr(adapter, spec.method)
    rows_obj = method(**spec.params)
    rows: list[dict[str, Any]]
    if isinstance(rows_obj, list):
        rows = [_row_dict(r) for r in rows_obj]
    elif isinstance(rows_obj, dict):
        rows = [rows_obj]
    elif rows_obj is None:
        rows = []
    else:
        rows = [_row_dict(rows_obj)]
    return {"rows": rows, "count": len(rows)}


# ----- CLI ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", help="restrict to one source (customs/fred/oecd/...)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--source-sleep",
        type=float,
        default=None,
        help="seconds to sleep between calls for the given --source (e.g. 4 for pytrends)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    # Per-source default delays — pytrends is rate-limited heavily by Google.
    DEFAULT_SLEEP = {"pytrends": 4.0, "un_comtrade": 0.8}

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    specs = specs_by_source(args.source) if args.source else all_specs()
    logger.info("collector start | %d specs | source=%s", len(specs), args.source or "ALL")

    if args.dry_run:
        for s in specs:
            print(f"  {s.source:12} {s.slug:60} {s.note}")
        return 0

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_entries: list[dict[str, Any]] = []

    # Cache adapter instances per source
    adapters: dict[str, Any] = {}
    fail_count = 0

    for i, spec in enumerate(specs, 1):
        try:
            if spec.source not in adapters:
                adapters[spec.source] = _load_adapter(spec.source)
            adapter = adapters[spec.source]
            t0 = time.perf_counter()
            res = collect_one(adapter, spec)
            elapsed = (time.perf_counter() - t0) * 1000
            target = OUT_ROOT / spec.source / f"{spec.slug}.csv"
            _write_csv(res["rows"], target)
            entry = {
                "spec": spec.slug,
                "source": spec.source,
                "sector": spec.sector,
                "name": spec.name,
                "rows": res["count"],
                "ms": round(elapsed, 1),
                "ok": True,
                "path": str(target.relative_to(OUT_ROOT)),
                "params": spec.params,
                "note": spec.note,
            }
            logger.info(
                "[%3d/%d] %s OK %4d rows %6.0fms — %s",
                i, len(specs), spec.source.ljust(12), res["count"], elapsed, spec.slug,
            )
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            entry = {
                "spec": spec.slug,
                "source": spec.source,
                "sector": spec.sector,
                "name": spec.name,
                "ok": False,
                "error": str(exc)[:300],
                "params": spec.params,
                "note": spec.note,
            }
            logger.warning(
                "[%3d/%d] %s FAIL — %s — %s",
                i, len(specs), spec.source.ljust(12), spec.slug, str(exc)[:120],
            )
            if args.verbose:
                logger.debug(traceback.format_exc())
        manifest_entries.append(entry)

        # Respect per-source rate limits between consecutive calls.
        sleep_s = args.source_sleep
        if sleep_s is None:
            sleep_s = DEFAULT_SLEEP.get(spec.source, 0.0)
        if sleep_s and i < len(specs):
            time.sleep(sleep_s)

    finished_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "started_at": started_at,
        "finished_at": finished_at,
        "total_specs": len(specs),
        "succeeded": len(specs) - fail_count,
        "failed": fail_count,
        "entries": manifest_entries,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info(
        "collector done | %d/%d OK | manifest: %s",
        len(specs) - fail_count, len(specs), MANIFEST_PATH,
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
