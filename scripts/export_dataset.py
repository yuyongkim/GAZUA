"""Export the WICS-34 매트릭스 dataset for sharing / academic use.

Output:
  ./exports/ky_matrix_20260426.zip
    ├ datasheet.md          — methodology, license, source list
    ├ matrix_summary.csv    — 340 cells × {sector, indicator, source, fill, latest, z}
    ├ sector_strength.csv   — 34 sectors × {rank, score, n_filled}
    ├ alerts.csv
    ├ disk_inventory.csv
    └ raw/                  — symlink/copy to ~/.gazua/data/sectors/ (large; opt-out)
"""
from __future__ import annotations

import csv
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "core"))
sys.path.insert(0, str(ROOT / "packages" / "adapters"))

EXPORT_DIR = ROOT / "exports"
EXPORT_DIR.mkdir(exist_ok=True)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main() -> None:
    today = date.today().strftime("%Y%m%d")
    out_zip = EXPORT_DIR / f"ky_matrix_{today}.zip"
    print(f"[export] target: {out_zip}")

    work = EXPORT_DIR / f"_stage_{today}"
    work.mkdir(exist_ok=True)

    # 1. Matrix summary
    from ky_core.scoring.macro_sector_strength import (
        _parse_map, _disk_index, _resolve_cell, _read_values, _zscore_detrended,
    )
    cells = _parse_map()
    csvs = _disk_index()
    summary_rows = []
    for c in cells:
        match = _resolve_cell(c, csvs)
        if match:
            folder, slug = match
            values = _read_values(folder, slug)
            z = _zscore_detrended(values)
        else:
            folder = slug = ""
            values = []
            z = None
        summary_rows.append({
            "cell_id": c["cell_id"],
            "sector_num": c["sector_num"],
            "sector_name": c["sector_name"],
            "indicator": c["indicator"],
            "source": c["source_raw"],
            "category": c["category"],
            "leadlag": c["leadlag"],
            "filled": "Y" if match else "N",
            "folder": folder, "slug": slug,
            "rows": len(values),
            "z_yoy": round(z, 3) if z is not None else "",
        })
    write_csv(work / "matrix_summary.csv", summary_rows,
              ["cell_id", "sector_num", "sector_name", "indicator", "source",
               "category", "leadlag", "filled", "folder", "slug", "rows", "z_yoy"])
    print(f"  matrix_summary: {len(summary_rows)} rows")

    # 2. Sector strength
    from ky_core.scoring.macro_sector_strength import compute_macro_strength
    strengths = compute_macro_strength(method="detrended")
    s_rows = []
    for s in strengths:
        s_rows.append({
            "rank": s.rank, "sector_num": s.sector_num,
            "sector_name": s.sector_name, "score": s.score,
            "n_filled": s.n_filled, "n_total": s.n_total,
            "fill_pct": s.fill_pct,
            "top_signal_1": s.top_signals[0]["indicator"] if s.top_signals else "",
            "top_signal_1_z": s.top_signals[0]["z"] if s.top_signals else "",
        })
    write_csv(work / "sector_strength.csv", s_rows,
              ["rank", "sector_num", "sector_name", "score", "n_filled", "n_total",
               "fill_pct", "top_signal_1", "top_signal_1_z"])
    print(f"  sector_strength: {len(s_rows)} rows")

    # 3. Alerts
    from ky_core.scoring.alerts import compute_alerts
    alerts = compute_alerts(z_threshold=2.0)
    a_rows = [{
        "cell_id": a.cell_id, "sector_num": a.sector_num,
        "sector_name": a.sector_name, "indicator": a.indicator,
        "z": a.z, "direction": a.direction, "severity": a.severity,
        "leadlag": a.leadlag,
    } for a in alerts]
    write_csv(work / "alerts.csv", a_rows,
              ["cell_id", "sector_num", "sector_name", "indicator", "z",
               "direction", "severity", "leadlag"])
    print(f"  alerts: {len(a_rows)} rows")

    # 4. Datasheet
    datasheet = f"""# WICS-34 Sector Indicator Matrix — Datasheet

**Date**: {date.today().isoformat()}
**Coverage**: 34 WICS Mid sectors × 10 indicators per sector = 340 cells
**Time depth**: ~30 years (1995-2026), varies by source
**Disk**: ~1.2 GB raw CSV (not included in this zip; download separately)

## Sources
| Source | License | Notes |
|---|---|---|
| FRED (US Federal Reserve) | public domain | macro time-series |
| ECOS (BOK) | open API, free | Korean macro |
| KOSIS (KO Statistics) | open API, free | industry indicators |
| customs (TRASS) | data.go.kr, free | HS-level trade |
| KIS (Korea Investment) | broker API, account required | KRX index |
| DART (FSS) | open API, free | listed corp filings |
| EIA | public domain | US energy |
| OECD | open API, free | global CLI |
| World Bank | open API, free | GDP, demographics |
| pytrends (Google) | unofficial, rate-limited | search interest |
| CFTC | public domain | COT |
| UN Comtrade | preview tier free | global trade |
| yfinance | unofficial Yahoo Finance | global ETF/futures |

## Methodology
1. Cell-level coverage verified via `scripts/verify_cells_v4.py`
2. Z-score: detrended (YoY% change) — removes inflation bias
3. Sector strength: weighted mean z (lead 1.5x / co 1.0x / lag 0.5x)
4. Filled = source-matched + ≥100 rows on disk

## Files in this zip
- `matrix_summary.csv` — per-cell summary
- `sector_strength.csv` — 34 sectors ranked
- `alerts.csv` — current |z| ≥ 2.0 cells
- `datasheet.md` — this file

## Citation
If you use this dataset for research, please cite:
> ky-platform WICS-34 Sector Indicator Matrix, {date.today().year}, internal release.

## License
- Code: see repository LICENSE
- Data: subject to original source licenses (mostly open-data)
"""
    (work / "datasheet.md").write_text(datasheet, encoding="utf-8")

    # 5. Zip everything
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in work.iterdir():
            zf.write(f, arcname=f.name)
    print(f"[export] done: {out_zip} ({out_zip.stat().st_size / 1024:.1f} KB)")
    print(f"[export] sample: unzip -l {out_zip}")


if __name__ == "__main__":
    main()
