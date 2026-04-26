"""Daily briefing — 매일 EOD 후 1페이지 요약 자동 생성 (LLM 없이 rule-based).

데이터:
- macro_sector_strength (TOP 5 / BOTTOM 5)
- alerts (|z| >= 2.0)
- macro_cycle (현재 국면)
- leader_stocks (TOP 10)
- 직전 24h matrix delta (향후 추가)

Output: 마크다운 + 구조화된 dict.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BriefingSection:
    title: str
    body_md: str
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DailyBriefing:
    as_of: str
    headline: str            # one-line summary
    cycle_label: str | None  # 현재 매크로 국면
    sections: list[BriefingSection]
    full_markdown: str


def generate_briefing() -> DailyBriefing:
    """Generate today's briefing using all scoring modules."""
    from ky_core.scoring.macro_sector_strength import compute_macro_strength
    from ky_core.scoring.alerts import compute_alerts
    from ky_core.scoring.macro_cycle import compute_macro_cycle

    sections: list[BriefingSection] = []

    # 1) Cycle
    try:
        cycle = compute_macro_cycle(n_clusters=4)
        cycle_label = cycle.label_for_current
        sections.append(BriefingSection(
            title="현재 매크로 국면",
            body_md=f"K-means 4-cluster 기준: **{cycle_label or '판정불가'}** "
                   f"(최근 {cycle.n_quarters}분기 대비)",
        ))
    except Exception as e:
        cycle_label = None
        sections.append(BriefingSection(title="매크로 국면", body_md=f"_계산 실패: {e}_"))

    # 2) Sector strength
    try:
        strengths = compute_macro_strength(method="detrended")
        top5 = strengths[:5]
        bot5 = strengths[-5:]
        body = "**TOP 5 강세**\n"
        for s in top5:
            body += f"- {s.rank}. [{s.sector_num}] **{s.sector_name}** (z={s.score:+.2f})\n"
        body += "\n**BOTTOM 5 약세**\n"
        for s in bot5:
            body += f"- {s.rank}. [{s.sector_num}] {s.sector_name} (z={s.score:+.2f})\n"
        sections.append(BriefingSection(
            title="섹터 강도 랭킹 (YoY% z-score)",
            body_md=body,
            rows=[{"rank": s.rank, "sector": s.sector_name,
                   "score": s.score, "n_filled": s.n_filled} for s in strengths[:10]],
        ))
        # Headline candidate
        headline = (f"매크로 국면: {cycle_label or '판정불가'} · "
                   f"강세 #{top5[0].sector_num} {top5[0].sector_name} (+{top5[0].score:.2f}) · "
                   f"약세 #{bot5[0].sector_num} {bot5[0].sector_name} ({bot5[0].score:+.2f})")
    except Exception as e:
        headline = f"브리핑 일부 실패: {e}"
        sections.append(BriefingSection(title="섹터 강도", body_md=f"_계산 실패: {e}_"))

    # 3) Alerts
    try:
        alerts = compute_alerts(z_threshold=2.0)
        body = ""
        for a in alerts[:10]:
            arrow = "↑" if a.direction == "up" else "↓"
            body += (f"- {arrow} [{a.sector_num}] {a.sector_name} — "
                    f"**{a.indicator}** z={a.z:+.1f} ({a.severity})\n")
        if not body:
            body = "_금일 |z| ≥ 2.0 알림 없음_\n"
        sections.append(BriefingSection(
            title=f"오늘의 알림 ({len(alerts)} cells, |z| ≥ 2.0)",
            body_md=body,
            rows=[{"cell_id": a.cell_id, "indicator": a.indicator,
                   "sector_name": a.sector_name, "z": a.z,
                   "severity": a.severity, "direction": a.direction}
                  for a in alerts[:20]],
        ))
    except Exception as e:
        sections.append(BriefingSection(title="알림", body_md=f"_계산 실패: {e}_"))

    # 4) Leader stocks
    try:
        from ky_core.scoring.leader_stock import scan_leader_stocks
        leaders = scan_leader_stocks(top_n=10)
        body = ""
        for L in leaders:
            body += (f"- #{L.rank} **{L.ticker}** {L.name} "
                    f"({L.wics_sector_name}) score={L.leader_score:.2f} — {L.reason[:80]}\n")
        if not body:
            body = "_종목 점수 산출 실패_"
        sections.append(BriefingSection(
            title="주도주 후보 TOP 10",
            body_md=body,
            rows=[L.to_dict() for L in leaders],
        ))
    except Exception as e:
        sections.append(BriefingSection(title="주도주", body_md=f"_계산 실패: {e}_"))

    # 5) Compose markdown
    full = f"# 일일 브리핑 — {time.strftime('%Y-%m-%d')}\n\n"
    full += f"**{headline}**\n\n"
    for s in sections:
        full += f"## {s.title}\n\n{s.body_md}\n\n"

    return DailyBriefing(
        as_of=time.strftime("%Y-%m-%d %H:%M"),
        headline=headline,
        cycle_label=cycle_label,
        sections=sections,
        full_markdown=full,
    )
