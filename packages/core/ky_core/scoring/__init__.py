"""Macro / fundamental scoring layer.

매트릭스 데이터 (FRED/ECOS/KOSIS/customs/DART/...) 기반 섹터·종목 점수.
가격 기반 점수는 ``ky_core.scanning.sector_strength`` (RS/등락률) 참고 — 별개.

Modules:
  - macro_sector_strength: WICS 34섹터 × 10지표 z-score → 섹터 강도 점수
"""
