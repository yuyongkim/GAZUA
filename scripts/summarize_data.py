"""Disk-truth summary of ~/.gazua/data/sectors/.

Scans the actual CSVs on disk (instead of the run-level manifest, which
only reflects the most recent collector invocation) and writes a unified
manifest with every series we currently have, regardless of how it got
there. Also prints a per-source / per-sector summary table.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path.home() / ".gazua" / "data" / "sectors"
OUT_REPO = Path(__file__).resolve().parent.parent / "data" / "sectors_unified_manifest.json"


def _row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return max(sum(1 for _ in csv.reader(f)) - 1, 0)
    except Exception:
        return -1


def _scan() -> list[dict]:
    entries: list[dict] = []
    if not ROOT.is_dir():
        return entries
    for path in sorted(ROOT.rglob("*.csv")):
        if path.name.startswith("_"):
            continue
        # source key — first directory under ROOT (e.g. customs, fred, imported/ecos)
        rel = path.relative_to(ROOT)
        parts = rel.parts
        source = parts[0] if len(parts) > 1 else "(root)"
        if source == "imported" and len(parts) > 2:
            source = f"imported_{parts[1]}"
        slug = path.stem
        entries.append(
            {
                "source": source,
                "slug": slug,
                "path": str(rel).replace("\\", "/"),
                "rows": _row_count(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def main() -> int:
    entries = _scan()
    if not entries:
        print(f"no CSVs under {ROOT}")
        return 1

    # Per-source aggregates
    per_source: dict[str, dict] = defaultdict(
        lambda: {"files": 0, "rows": 0, "bytes": 0}
    )
    for e in entries:
        s = e["source"]
        per_source[s]["files"] += 1
        per_source[s]["rows"] += max(e["rows"], 0)
        per_source[s]["bytes"] += e["size_bytes"]

    total_files = sum(v["files"] for v in per_source.values())
    total_rows = sum(v["rows"] for v in per_source.values())
    total_bytes = sum(v["bytes"] for v in per_source.values())

    print(f"\n{'=' * 60}")
    print(f"DISK-TRUTH SUMMARY  ({ROOT})")
    print("=" * 60)
    print(f"{'source':22} {'files':>6} {'rows':>10} {'size':>10}")
    print("-" * 60)
    for source in sorted(per_source.keys()):
        v = per_source[source]
        size_h = _humanise(v["bytes"])
        print(f"{source:22} {v['files']:>6d} {v['rows']:>10d} {size_h:>10}")
    print("-" * 60)
    print(f"{'TOTAL':22} {total_files:>6d} {total_rows:>10d} {_humanise(total_bytes):>10}")
    print()

    # Write unified manifest
    OUT_REPO.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPO.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_root": str(ROOT),
                "total_files": total_files,
                "total_rows": total_rows,
                "total_bytes": total_bytes,
                "per_source": dict(per_source),
                "entries": entries,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"unified manifest → {OUT_REPO}")
    return 0


def _humanise(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < step:
            return f"{val:.1f}{unit}"
        val /= step
    return f"{val:.1f}TB"


if __name__ == "__main__":
    raise SystemExit(main())
