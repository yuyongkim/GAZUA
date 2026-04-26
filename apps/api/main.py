"""GAZUA FastAPI entrypoint.

Run locally:
    uvicorn main:app --reload --port 8300
Or via the root helper:
    ./scripts/server.sh start
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routers.research import router as research_router

# Make packages/core importable.
_PKG_CORE = Path(__file__).resolve().parents[2] / "packages" / "core"
_PKG_ADAPT = Path(__file__).resolve().parents[2] / "packages" / "adapters"
for p in (_PKG_CORE, _PKG_ADAPT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="GAZUA API",
        version="0.1.0",
        description="가즈아에 데이터를 얹다 — WICS 34섹터 × 10지표 × 30년 매트릭스 API.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "ok": True,
            "data": {
                "status": "ok",
                "version": "0.1.0",
                "shared_env_loaded": settings.shared_env_present,
            },
            "error": None,
        }

    # Single mount: research/sector-indicators (24 endpoints)
    app.include_router(research_router, prefix="/api/v1/research", tags=["research"])

    return app


app = create_app()
