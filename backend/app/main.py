"""
FastAPI application factory for Operation Abhedya-Chakra.

Phase 1 exposes only the /health endpoint.
Future phases will mount graph, detection, and evidence routers here.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.routes.detection import router as detection_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Abhedya-Chakra",
        description="Financial forensics engine — Phase 1: Ingestion",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.include_router(health_router)
    app.include_router(detection_router)

    @app.on_event("startup")
    async def _startup() -> None:
        logger.info("Abhedya-Chakra API started (Phase 1)")

    return app


app = create_app()
