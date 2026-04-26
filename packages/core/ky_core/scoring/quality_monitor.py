"""Data quality monitor — 매일 cell-level verifier 결과를 audit DB에 저장.

회귀 탐지: 어제 fill_pct=98.2% → 오늘 95% 시 alert.
DB 위치: ky.db (audit_log table 활용)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"


@dataclass
class QualitySnapshot:
    as_of: str
    total_cells: int
    filled_cells: int
    fill_pct: float
    underbacked_cells: list[str]   # cell_ids
    sources_health: dict[str, dict[str, Any]]  # source → {csvs, max_rows}


def _scan_disk() -> dict[str, dict[str, Any]]:
    """source folder 별 헬스 정보."""
    DATA = Path.home() / ".gazua" / "data" / "sectors"
    out: dict[str, dict[str, Any]] = {}
    if not DATA.exists():
        return out
    for d in DATA.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if d.name == "imported":
            for sub in d.iterdir():
                if sub.is_dir():
                    csvs = list(sub.glob("*.csv"))
                    if csvs:
                        out[f"imported/{sub.name}"] = {
                            "csvs": len(csvs),
                            "max_rows": _max_rows(csvs),
                            "size_mb": round(sum(c.stat().st_size for c in csvs) / 1024 / 1024, 1),
                        }
            continue
        csvs = list(d.glob("*.csv"))
        if csvs:
            out[d.name] = {
                "csvs": len(csvs),
                "max_rows": _max_rows(csvs),
                "size_mb": round(sum(c.stat().st_size for c in csvs) / 1024 / 1024, 1),
            }
    return out


def _max_rows(csvs: list[Path]) -> int:
    m = 0
    for c in csvs[:50]:  # cap to first 50 to bound cost
        try:
            n = sum(1 for _ in c.open(encoding="utf-8", errors="ignore")) - 1
            m = max(m, n)
        except Exception:
            continue
    return max(m, 0)


def take_snapshot() -> QualitySnapshot:
    """Run cell verifier + capture stats."""
    # Reuse the verify_cells_v4 logic inline
    import re as _re
    DATA = Path.home() / ".gazua" / "data" / "sectors"
    MAP = Path(__file__).resolve().parents[4] / "docs" / "SECTOR_INDICATOR_MAP.md"
    text = MAP.read_text(encoding="utf-8")
    sec_re = _re.compile(r"^##\s*\[(\d{2})\]\s+(.+?)\s*$", _re.M)
    row_re = _re.compile(
        r"^\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
        _re.M,
    )
    matches = list(sec_re.finditer(text))
    cells = []
    for i, m in enumerate(matches):
        chunk = text[m.end():(matches[i+1].start() if i+1 < len(matches) else len(text))]
        for row in row_re.findall(chunk):
            idx = row[0]
            cells.append({"cell_id": f"{m.group(1)}_{idx.zfill(2)}",
                         "sector_num": m.group(1)})

    # Reuse macro_sector_strength's resolver for per-cell match
    from ky_core.scoring.macro_sector_strength import (
        _disk_index, _resolve_cell, _parse_map,
    )
    parsed = _parse_map()
    csvs = _disk_index()
    filled = 0
    underbacked: list[str] = []
    for c in parsed:
        match = _resolve_cell(c, csvs)
        if match:
            filled += 1
        else:
            underbacked.append(c["cell_id"])

    sources_health = _scan_disk()

    return QualitySnapshot(
        as_of=time.strftime("%Y-%m-%d %H:%M"),
        total_cells=len(parsed),
        filled_cells=filled,
        fill_pct=round(filled * 100.0 / max(1, len(parsed)), 2),
        underbacked_cells=underbacked,
        sources_health=sources_health,
    )


def _ensure_audit_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS quality_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taken_at TEXT NOT NULL,
            total_cells INTEGER,
            filled_cells INTEGER,
            fill_pct REAL,
            underbacked_json TEXT,
            sources_json TEXT
        )
    """)


def store_snapshot(snap: QualitySnapshot) -> int:
    """Persist a snapshot. Returns the inserted row id."""
    con = sqlite3.connect(DB_PATH)
    try:
        _ensure_audit_table(con)
        cur = con.execute(
            "INSERT INTO quality_snapshots (taken_at, total_cells, filled_cells, "
            "fill_pct, underbacked_json, sources_json) VALUES (?,?,?,?,?,?)",
            (snap.as_of, snap.total_cells, snap.filled_cells, snap.fill_pct,
             json.dumps(snap.underbacked_cells, ensure_ascii=False),
             json.dumps(snap.sources_health, ensure_ascii=False)),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def history(limit: int = 30) -> list[dict[str, Any]]:
    """최근 N개 snapshot."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        _ensure_audit_table(con)
        rows = con.execute(
            "SELECT * FROM quality_snapshots ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "taken_at": r["taken_at"],
                "total_cells": r["total_cells"], "filled_cells": r["filled_cells"],
                "fill_pct": r["fill_pct"],
                "underbacked": json.loads(r["underbacked_json"]) if r["underbacked_json"] else [],
                "sources": json.loads(r["sources_json"]) if r["sources_json"] else {},
            })
        return out
    finally:
        con.close()


def detect_regression(snap: QualitySnapshot, prev: dict[str, Any] | None = None,
                      threshold_pct: float = 1.0) -> dict[str, Any]:
    """직전 스냅샷 대비 fill_pct 회귀 감지.

    Returns: {regressed: bool, prev_pct, cur_pct, delta, msg}
    """
    if prev is None:
        h = history(limit=2)
        if len(h) < 2:
            return {"regressed": False, "msg": "no prior snapshot"}
        prev = h[1]
    prev_pct = prev.get("fill_pct", 100.0)
    delta = snap.fill_pct - prev_pct
    regressed = delta < -threshold_pct
    msg = (
        f"REGRESSION: fill_pct {prev_pct:.2f}% → {snap.fill_pct:.2f}% (Δ {delta:+.2f}%)"
        if regressed
        else f"OK: fill_pct {prev_pct:.2f}% → {snap.fill_pct:.2f}% (Δ {delta:+.2f}%)"
    )
    return {
        "regressed": regressed,
        "prev_pct": prev_pct,
        "cur_pct": snap.fill_pct,
        "delta": round(delta, 2),
        "msg": msg,
    }
