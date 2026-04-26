"""관세청 무역통계 adapter (data.go.kr 1220000 service group).

Covers six APIs (one universal data.go.kr key):
  1. 관세청_품목별 국가별 수출입실적(GW) — HS×country, monthly
  2. 관세청_품목별 수출입실적(GW)        — HS only, monthly
  3. 관세청_수출 주요품목별 10일 단위 잠정치 통계
  4. 관세청_수입 주요품목별 10일 단위 잠정치 통계
  5. 관세청_수출 주요국가별 10일 단위 잠정치 통계
  6. 관세청_수입 주요국가별 10일 단위 잠정치 통계
"""
from __future__ import annotations

from ky_adapters.customs.client import (
    CustomsAdapter,
    CustomsObservation,
)

__all__ = ["CustomsAdapter", "CustomsObservation"]
