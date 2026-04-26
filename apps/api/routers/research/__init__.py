"""GAZUA research router — sector-indicators 매트릭스 + 분석.

24 endpoints under /api/v1/research/sector-indicators/*.
"""
from __future__ import annotations

from fastapi import APIRouter

from routers.research.sector_indicators import router as _sector_indicators_router

router = APIRouter()
router.include_router(_sector_indicators_router)
