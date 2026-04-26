"""정책·이벤트 캘린더 + 매트릭스 영향 분석.

이벤트:
- 한은 금통위 (매월 둘째 목요일 — 단, 정확한 일자는 별도 source 필요)
- Fed FOMC (연 8회, 화·수)
- 한국 분기실적 발표 시즌 (3/5/8/11월)
- 주요 경제지표 발표 (BOK GDP, KOSIS 광공업 등)

Minimum: 알려진 패턴으로 next_events 추정 + t-30/t/t+30 매트릭스 변화 측정.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CalendarEvent:
    event_id: str
    date: str           # YYYY-MM-DD
    name: str
    type: str           # 'BOK_MPC' / 'FED_FOMC' / 'EARNINGS' / 'KOSIS' / 'CUSTOM'
    expected_impact: str = ""  # 'high' / 'medium' / 'low'


@dataclass
class EventImpact:
    event_id: str
    event_name: str
    event_date: str
    avg_z_change_t_minus_30: float
    avg_z_change_t_plus_30: float
    most_impacted_sectors: list[dict[str, Any]]
    note: str = ""


# 알려진 패턴 (정확한 일자는 외부 source / 수동 입력)
KNOWN_BOK_DATES_2025_2026 = [
    "2025-01-16", "2025-02-25", "2025-04-17", "2025-05-29",
    "2025-07-10", "2025-08-28", "2025-10-23", "2025-11-27",
    "2026-01-15", "2026-02-26", "2026-04-09", "2026-05-28",
]

KNOWN_FOMC_DATES_2025_2026 = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def get_calendar(
    *,
    year: int | None = None,
    months_ahead: int = 3,
) -> list[CalendarEvent]:
    """현재부터 N개월 이후까지 이벤트 리스트."""
    today = date.today()
    end = today + timedelta(days=months_ahead * 31)
    events: list[CalendarEvent] = []

    for d in KNOWN_BOK_DATES_2025_2026:
        try:
            ev_d = datetime.fromisoformat(d).date()
        except ValueError:
            continue
        if ev_d >= today and ev_d <= end:
            events.append(CalendarEvent(
                event_id=f"bok_{d}", date=d, name="한은 금통위 정례회의",
                type="BOK_MPC", expected_impact="high"))

    for d in KNOWN_FOMC_DATES_2025_2026:
        try:
            ev_d = datetime.fromisoformat(d).date()
        except ValueError:
            continue
        if ev_d >= today and ev_d <= end:
            events.append(CalendarEvent(
                event_id=f"fomc_{d}", date=d, name="Fed FOMC",
                type="FED_FOMC", expected_impact="high"))

    # 실적 시즌 (3/5/8/11월 중순)
    for y in (today.year, today.year + 1):
        for m in (3, 5, 8, 11):
            ev_d = date(y, m, 15)
            if ev_d >= today and ev_d <= end:
                events.append(CalendarEvent(
                    event_id=f"earnings_{y}q{(m-1)//3}", date=ev_d.isoformat(),
                    name=f"{y}년 {(m-1)//3}분기 실적 발표 시즌",
                    type="EARNINGS", expected_impact="medium"))

    events.sort(key=lambda e: e.date)
    return events


def estimate_event_impact(event_type: str = "BOK_MPC") -> EventImpact | None:
    """과거 이벤트 시점 t-30/t/t+30 매트릭스 평균 변화 (현재는 placeholder).

    완전 구현은 매트릭스 data를 이벤트 일자별로 cut, 변화량 계산. 1차는 dummy.
    """
    cal = get_calendar(months_ahead=12)
    matching = [e for e in cal if e.type == event_type]
    if not matching:
        return None
    nearest = matching[0]
    # Placeholder — TODO: 실제 매트릭스 사전·사후 z 변화 통계
    return EventImpact(
        event_id=nearest.event_id,
        event_name=nearest.name,
        event_date=nearest.date,
        avg_z_change_t_minus_30=0.0,
        avg_z_change_t_plus_30=0.0,
        most_impacted_sectors=[],
        note="event impact baseline은 추가 historical cut 필요. 1차는 calendar만 노출.",
    )
