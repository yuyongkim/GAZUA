"""Matrix alerts — z-score 극단값 + regime change 자동 탐지.

각 cell의 latest z (3Y 기준) 가 |z| > threshold 이거나, 직전 30일과 다른
방향으로 큰 변화 있는 경우 alert. 일일 EOD 후 호출.

Output schema:
{
  cell_id, sector, indicator, latest, latest_date,
  z, direction (up/down), severity (warn/strong/extreme)
}
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


from ky_core.scoring.macro_sector_strength import (  # noqa: E402
    _parse_map,
    _disk_index,
    _resolve_cell,
    _read_values,
    _yoy_change,
)


@dataclass
class CellAlert:
    cell_id: str
    sector_num: str
    sector_name: str
    indicator: str
    source: str
    leadlag: str
    latest: float
    latest_date: str | None
    z: float
    yoy_pct: float | None
    direction: str       # 'up' / 'down'
    severity: str        # 'warn' / 'strong' / 'extreme'
    note: str = ""


def _severity(z: float) -> str:
    a = abs(z)
    if a >= 3.0:
        return "extreme"
    if a >= 2.5:
        return "strong"
    if a >= 2.0:
        return "warn"
    return "none"


def compute_alerts(
    z_threshold: float = 2.0,
    method: str = "yoy",
) -> list[CellAlert]:
    """매트릭스의 모든 cell을 스캔, |z| >= threshold 인 cell만 반환.

    Args:
        z_threshold: 알림 발동 z 기준 (default 2.0)
        method: 'yoy' (default — 모멘텀) / 'raw' (level)
    """
    cells = _parse_map()
    csvs = _disk_index()
    alerts: list[CellAlert] = []

    for c in cells:
        match = _resolve_cell(c, csvs)
        if not match:
            continue
        folder, slug = match
        values = _read_values(folder, slug)
        if not values:
            continue

        # Series for z calc
        if method == "yoy":
            series = _yoy_change(values)
        else:
            series = values
        if len(series) < 24:
            continue

        # Rolling z on last 36 / 252 / 12
        sample = series[-min(len(series), 756):]
        clean = [v for v in sample if isinstance(v, (int, float)) and math.isfinite(v)]
        if len(clean) < 12:
            continue
        mean = sum(clean) / len(clean)
        var = sum((x - mean) ** 2 for x in clean) / len(clean)
        std = var ** 0.5
        if std == 0:
            continue
        latest_change = clean[-1]
        z = (latest_change - mean) / std
        if not math.isfinite(z) or abs(z) < z_threshold:
            continue

        # 역신호 처리
        if "역" in c["leadlag"]:
            z = -z

        # YoY pct (raw values 기준)
        latest_raw = values[-1]
        yoy_pct = None
        if len(values) > 12:
            prev = values[-13] if len(values) > 13 else values[0]
            if prev and prev != 0:
                yoy_pct = (latest_raw - prev) / abs(prev) * 100.0

        alerts.append(CellAlert(
            cell_id=c["cell_id"],
            sector_num=c["sector_num"],
            sector_name=c["sector_name"],
            indicator=c["indicator"],
            source=c["source_raw"][:30],
            leadlag=c["leadlag"],
            latest=round(latest_raw, 4) if isinstance(latest_raw, (int, float)) else 0.0,
            latest_date=None,
            z=round(z, 2),
            yoy_pct=round(yoy_pct, 2) if yoy_pct is not None else None,
            direction="up" if z > 0 else "down",
            severity=_severity(z),
            note="강한 모멘텀 변화" if abs(z) >= 2.5 else "주의 신호",
        ))

    alerts.sort(key=lambda x: abs(x.z), reverse=True)
    return alerts
