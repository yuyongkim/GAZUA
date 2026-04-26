"""Import existing time series from Economic_analysis/.

Rather than re-collecting series the user already has on disk
(``Economic_analysis/economic_indicator/data/raw/``), pick the most recent
versioned copy of each ECOS / FRED / KRX series and stage it under
``~/.gazua/data/sectors/imported/<source>/<slug>.csv`` so the manifest
+ collector treat it the same as live API output.

The schema in Economic_analysis is already normalized:
    date,indicator,value,unit,source,description,category

We pass the file through unchanged (just normalize the BOM and copy with a
clean name) so the downstream consumer doesn't need a per-source schema.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

logger = logging.getLogger("import_existing")

DEFAULT_SRC_ROOT = Path("C:/Users/USER/Desktop/Economic_analysis/economic_indicator/data/raw")
DEST_ROOT = Path.home() / ".gazua" / "data" / "sectors" / "imported"

# Files matching one of these prefixes get imported. The match is case-
# sensitive against the slug (everything before the timestamp suffix).
IMPORT_PREFIXES = ("ECOS_", "FRED_", "KRX_")

# Timestamp suffix pattern Economic_analysis appends (e.g. _20251108_194317.csv)
TIMESTAMP_RE = re.compile(r"_(\d{8})(?:_(\d{6}))?\.csv$", re.I)


def _slug_for(filename: str) -> Optional[tuple[str, str]]:
    """Return (source, slug) if the filename matches an import prefix."""
    m = TIMESTAMP_RE.search(filename)
    if not m:
        # Files without a timestamp (e.g. KRX_코스피지수_재수집.csv) — keep as-is.
        slug = filename[:-4] if filename.endswith(".csv") else filename
    else:
        slug = filename[: m.start()]
    for prefix in IMPORT_PREFIXES:
        if slug.startswith(prefix):
            source_label = prefix.rstrip("_").lower()
            return source_label, slug
    return None


def _pick_latest_per_slug(src_dir: Path) -> dict[tuple[str, str], Path]:
    """Group source-files by (source, slug) and return the newest copy."""
    best: dict[tuple[str, str], tuple[Path, str]] = {}
    for p in src_dir.iterdir():
        if not p.is_file() or not p.name.endswith(".csv"):
            continue
        match = _slug_for(p.name)
        if match is None:
            continue
        ts_m = TIMESTAMP_RE.search(p.name)
        ts = ts_m.group(0) if ts_m else "00000000_000000"
        existing = best.get(match)
        if existing is None or ts > existing[1]:
            best[match] = (p, ts)
    return {k: v[0] for k, v in best.items()}


def _row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            count = sum(1 for _ in r) - 1
        return max(count, 0)
    except Exception:
        return -1


# Best-effort: tag the imported series with a sector label so the manifest
# is consistent with the registry-driven specs. The mapping is rough — we
# only need it for downstream filtering.
_SECTOR_HINT = {
    "기준금리":"macro", "금리":"macro", "회사채":"macro", "국채금리":"macro",
    "M2":"macro", "통화량":"macro", "CD91":"macro",
    "고용률":"macro", "실업률":"macro", "물가지수":"macro",
    "소비자":"macro", "기업경기":"macro", "심리지수":"macro",
    "코스피":"market", "코스닥":"market",
    "FED":"macro", "Fed":"macro", "TREASURY":"macro", "OIL":"oil",
    "GDP":"macro", "DOLLAR":"macro", "DOW":"market", "SP500":"market",
    "NASDAQ":"market", "PCE":"macro", "RETAIL":"retail",
    "INDUSTRIAL":"manufacturing", "CONSUMER":"macro", "PAYROLL":"macro",
    "UNEMPLOYMENT":"macro", "LABOR":"macro", "CPI":"macro",
}


def _guess_sector(slug: str) -> str:
    for key, sec in _SECTOR_HINT.items():
        if key.lower() in slug.lower():
            return sec
    return "macro"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--src", type=Path, default=DEFAULT_SRC_ROOT)
    p.add_argument("--dest", type=Path, default=DEST_ROOT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.src.is_dir():
        logger.error("source dir not found: %s", args.src)
        return 1

    picks = _pick_latest_per_slug(args.src)
    logger.info("scanning %s | %d unique series found", args.src, len(picks))

    if args.dry_run:
        for (source, slug), path in sorted(picks.items()):
            print(f"  {source:6} {slug:55} ← {path.name}")
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()

    for (source, slug), path in sorted(picks.items()):
        out_dir = args.dest / source
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{slug}.csv"
        shutil.copyfile(path, target)
        rc = _row_count(target)
        sector = _guess_sector(slug)
        logger.info("%s/%s ← %s (%d rows)", source, slug, path.name, rc)
        manifest_entries.append(
            {
                "spec": slug,
                "source": f"imported_{source}",
                "sector": sector,
                "name": slug,
                "rows": rc,
                "ok": True,
                "path": str(target.relative_to(args.dest.parent)),
                "imported_from": str(path),
            }
        )

    manifest_path = args.dest / "_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "total": len(manifest_entries),
                "source_root": str(args.src),
                "entries": manifest_entries,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    logger.info("imported %d series → %s", len(manifest_entries), args.dest)
    logger.info("manifest: %s", manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
