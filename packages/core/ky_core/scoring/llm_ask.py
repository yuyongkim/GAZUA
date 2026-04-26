"""LLM 자연어 인터페이스 — 매트릭스 컨텍스트로 Claude/OpenAI에 질문.

사용:
  ask_matrix("지금 화학 섹터에 들어가도 되는가?")
  → 매트릭스 + 사이클 + 강도 컨텍스트 → LLM 답변

LLM 호출은 anthropic SDK 또는 openai SDK 사용. API 키 없으면 rule-based fallback.
prompt caching: 매트릭스 컨텍스트는 system prompt에 넣어 재사용.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AskResponse:
    question: str
    answer: str
    used_llm: str    # 'anthropic' / 'openai' / 'rule-based'
    context_chars: int
    cached: bool = False


def _build_context() -> str:
    """매트릭스 + 사이클 + 강도 + alerts → LLM 컨텍스트 문자열."""
    from ky_core.scoring.macro_sector_strength import compute_macro_strength
    from ky_core.scoring.macro_cycle import compute_macro_cycle
    from ky_core.scoring.alerts import compute_alerts

    parts = []

    # Cycle
    try:
        cycle = compute_macro_cycle(n_clusters=4)
        parts.append(
            f"## 매크로 사이클\n현재 국면: {cycle.label_for_current or '미정'} "
            f"(K-means {cycle.n_clusters}-cluster, {cycle.n_quarters} quarters)\n"
        )
    except Exception:
        pass

    # Strength
    try:
        strengths = compute_macro_strength(method="detrended")
        parts.append("## 섹터 강도 (YoY% z-score)")
        parts.append("Top 10:")
        for s in strengths[:10]:
            parts.append(f"  #{s.rank} [{s.sector_num}] {s.sector_name}  "
                        f"score={s.score:+.2f}  filled={s.n_filled}/{s.n_total}")
        parts.append("Bottom 5:")
        for s in strengths[-5:]:
            parts.append(f"  #{s.rank} [{s.sector_num}] {s.sector_name}  score={s.score:+.2f}")
    except Exception:
        pass

    # Alerts
    try:
        alerts = compute_alerts(z_threshold=2.5)
        parts.append(f"\n## 강한 알림 (|z| ≥ 2.5, {len(alerts)} cells)")
        for a in alerts[:15]:
            arrow = "↑" if a.direction == "up" else "↓"
            parts.append(f"  {arrow} [{a.sector_num}] {a.sector_name} — "
                        f"{a.indicator} z={a.z:+.1f} ({a.severity})")
    except Exception:
        pass

    return "\n".join(parts)


def _rule_based_answer(q: str, context: str) -> str:
    """LLM API 없을 때 패턴 매칭 답변."""
    q_lower = q.lower()

    # 섹터 강도 직접 조회
    if "강세" in q or "top" in q_lower or "강한" in q:
        for line in context.splitlines():
            if line.strip().startswith("#1 "):
                return f"현재 가장 강한 섹터: {line.strip()}\n\n전체 컨텍스트:\n{context[:2000]}"

    if "약세" in q or "bottom" in q_lower or "약한" in q:
        bottom_lines = [l for l in context.splitlines() if "Bottom" in l or "#34" in l or "#33" in l]
        return f"약세 섹터:\n" + "\n".join(bottom_lines) + f"\n\n전체:\n{context[:1500]}"

    if "사이클" in q or "국면" in q:
        for line in context.splitlines():
            if "현재 국면" in line:
                return line + "\n\n4-cluster K-means 기반 분류"

    if "알림" in q or "alert" in q_lower:
        alert_lines = [l for l in context.splitlines() if l.strip().startswith(("↑", "↓"))]
        return f"강한 신호 ({len(alert_lines)} cells):\n" + "\n".join(alert_lines[:15])

    # 섹터 이름 매칭
    sectors = ["반도체", "화학", "자동차", "조선", "철강", "바이오", "건설",
               "금융", "은행", "보험", "부동산", "통신", "유통", "식품",
               "디스플레이", "비철", "기계"]
    for sec in sectors:
        if sec in q:
            relevant = [l for l in context.splitlines() if sec in l]
            return f"'{sec}' 관련:\n" + "\n".join(relevant[:10])

    return ("질문을 더 구체적으로 해 주시면 도움이 됩니다 (예: \"반도체 섹터 강세인가?\", "
            f"\"지금 사이클 국면\", \"강한 알림 보여줘\").\n\n현재 컨텍스트 일부:\n{context[:1500]}")


def ask_matrix(
    question: str,
    *,
    model: str | None = None,
    use_llm: bool = True,
) -> AskResponse:
    """매트릭스 컨텍스트로 자연어 질문에 답변.

    Args:
        question: 사용자 질문 (한국어/영어)
        model: 'claude-haiku-4-5-20251001' (default) / 'gpt-4o-mini' / etc
        use_llm: False면 rule-based만 사용 (API 키 없을 때)

    Returns:
        AskResponse
    """
    context = _build_context()

    if not use_llm:
        return AskResponse(
            question=question,
            answer=_rule_based_answer(question, context),
            used_llm="rule-based",
            context_chars=len(context),
        )

    # Try Anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            sys_prompt = (
                "당신은 한국 주식시장 매크로 분석가입니다. "
                "아래 매트릭스 컨텍스트만을 근거로 사용자 질문에 답변하세요. "
                "추측하지 말고, 데이터에 없는 정보는 '데이터 부족'이라고 답하세요. "
                "답변은 짧고 직설적으로, 핵심 수치와 함께 제시하세요.\n\n"
                f"## 매트릭스 컨텍스트 (2026-04-26)\n\n{context}"
            )
            resp = client.messages.create(
                model=model or "claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=[{
                    "type": "text", "text": sys_prompt,
                    "cache_control": {"type": "ephemeral"},  # prompt caching
                }],
                messages=[{"role": "user", "content": question}],
            )
            answer = resp.content[0].text if resp.content else "(응답 없음)"
            cached = (
                getattr(resp, "usage", None)
                and getattr(resp.usage, "cache_read_input_tokens", 0) > 0
            )
            return AskResponse(
                question=question, answer=answer,
                used_llm="anthropic", context_chars=len(context),
                cached=bool(cached),
            )
        except Exception as e:
            logger.warning("anthropic call failed: %s", e)

    # Try OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            sys_prompt = (
                "당신은 한국 주식시장 매크로 분석가입니다. "
                "아래 매트릭스 컨텍스트만을 근거로 사용자 질문에 답변하세요.\n\n"
                f"{context}"
            )
            resp = client.chat.completions.create(
                model=model or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": question},
                ],
                max_tokens=1024,
            )
            answer = resp.choices[0].message.content or "(응답 없음)"
            return AskResponse(
                question=question, answer=answer,
                used_llm="openai", context_chars=len(context),
            )
        except Exception as e:
            logger.warning("openai call failed: %s", e)

    # Fallback: rule-based
    return AskResponse(
        question=question,
        answer=_rule_based_answer(question, context),
        used_llm="rule-based (no API key)",
        context_chars=len(context),
    )
