"""Report how stale each core data store is and flag anything beyond threshold.

Covered tables:

- ``ohlcv``            — daily price bars (per-owner). Expected stale after 5 business days.
- ``observations``     — macro series (FRED, ECOS, KOSIS, …). Per-source freshness.
- ``filings``          — DART disclosures. Expected daily during business days.
- ``fnguide_snapshots``— per-symbol fundamental snapshots. Weekly refresh typical.

Usage::

    python scripts/check_data_freshness.py           # human-readable table
    python scripts/check_data_freshness.py --json    # machine-readable JSON

Exit codes:
    0 — all tables within their freshness budgets
    2 — at least one data source is stale
    3 — DB unreadable
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"

# Per-table freshness budgets, in days. Sources are checked individually where
# relevant because FRED lagging by 5 days is normal; KOSIS lagging by 60 days
# is also normal; but a daily feed lagging 30 days is a real alarm.
STALE_DAYS: Dict[str, int] = {
    "ohlcv": 5,
    "filings": 5,
    "fnguide_snapshots": 14,
    # Observations are split by source-class below.
}

OBSERVATION_SOURCE_BUDGETS: Dict[str, int] = {
    "fred": 7,
    "ecos": 35,          # Bank of Korea monthly series typically lag ~1 month
    "kosis_legacy_qp": 365,   # historic archive, only report not alarm
    "eia": 10,
    "commodity_legacy_qp": 365,
    "fred_legacy_qp": 365,
    "ecos_legacy_qp": 365,
    "quantking_legacy_qdb": 365,
    "eia_legacy_qdb": 365,
    "exim": 60,
}


def _days_since(date_str: str) -> int:
    """Return whole days between ``date_str`` (YYYY-MM-DD) and today (UTC)."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return -1
    today = datetime.now(timezone.utc).date()
    return (today - d).days


def audit_table(
    conn: sqlite3.Connection,
    table: str,
    date_col: str,
    budget_days: int,
    group_col: str | None = None,
) -> Dict[str, Any]:
    cur = conn.cursor()
    total = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if group_col:
        rows = cur.execute(
            f"SELECT {group_col}, MAX({date_col}), COUNT(*) "
            f"FROM {table} GROUP BY {group_col} ORDER BY {group_col}"
        ).fetchall()
        groups = []
        worst = 0
        for grp, latest, n in rows:
            lag = _days_since(latest) if latest else -1
            budget = OBSERVATION_SOURCE_BUDGETS.get(grp, budget_days) if table == "observations" else budget_days
            groups.append(
                {
                    "group": grp,
                    "latest_date": latest,
                    "row_count": n,
                    "lag_days": lag,
                    "budget_days": budget,
                    "stale": lag > budget if lag >= 0 else True,
                }
            )
            if lag > 0:
                worst = max(worst, lag)
        any_stale = any(g["stale"] for g in groups)
        return {
            "table": table,
            "row_count": total,
            "groups": groups,
            "any_stale": any_stale,
            "worst_lag_days": worst,
        }

    row = cur.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()
    latest = row[0] if row else None
    lag = _days_since(latest) if latest else -1
    return {
        "table": table,
        "row_count": total,
        "latest_date": latest,
        "lag_days": lag,
        "budget_days": budget_days,
        "stale": lag > budget_days if lag >= 0 else True,
    }


def build_report(db_path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        sections: List[Dict[str, Any]] = [
            audit_table(conn, "ohlcv", "date", STALE_DAYS["ohlcv"], group_col=None),
            audit_table(
                conn,
                "observations",
                "date",
                budget_days=30,
                group_col="source_id",
            ),
            audit_table(conn, "filings", "filed_at", STALE_DAYS["filings"], group_col=None),
            audit_table(
                conn,
                "fnguide_snapshots",
                "substr(fetched_at, 1, 10)",
                STALE_DAYS["fnguide_snapshots"],
                group_col=None,
            ),
        ]
    finally:
        conn.close()

    any_stale = any(
        s.get("stale") or s.get("any_stale") for s in sections
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "any_stale": any_stale,
        "sections": sections,
    }


def _print_human(report: Dict[str, Any]) -> None:
    print(f"ky.db freshness @ {report['generated_at']}")
    print(f"db: {report['db_path']}")
    print("-" * 72)
    for sec in report["sections"]:
        table = sec["table"]
        if "groups" in sec:
            status = "STALE" if sec["any_stale"] else "ok"
            print(f"[{status}] {table} — {sec['row_count']} rows, worst lag {sec['worst_lag_days']}d")
            for g in sec["groups"]:
                flag = "!" if g["stale"] else " "
                print(
                    f"   {flag} {g['group']:28s} latest={g['latest_date']} "
                    f"lag={g['lag_days']:>4}d (budget {g['budget_days']}d, n={g['row_count']})"
                )
        else:
            status = "STALE" if sec.get("stale") else "ok"
            print(
                f"[{status}] {table:22s} latest={sec.get('latest_date')} "
                f"lag={sec.get('lag_days')}d (budget {sec.get('budget_days')}d, "
                f"n={sec['row_count']})"
            )
    print("-" * 72)
    print("overall:", "STALE" if report["any_stale"] else "fresh")


def main(argv: List[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of table")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 3

    report = build_report(args.db)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 2 if report["any_stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
