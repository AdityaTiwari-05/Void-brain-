"""
FastAPI application factory for Operation Abhedya-Chakra.

Modules:
  A - Ingestion & Normalization
  B - Detection Engine (risk, hop trace, detection)
  C - Investigator UI backend (accounts, transactions, graph)
  D - AI Case Officer (evidence packet, case diary, freeze requisition)
"""

from __future__ import annotations

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.routes.detection import router as detection_router
from app.api.routes.accounts import router as accounts_router
from app.api.routes.transactions import router as transactions_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.investigations import router as investigations_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Abhedya-Chakra — Operation Financial Forensics Engine",
        description=(
            "Modules A+B+C+D: Ingestion → Mule Detection → Investigation UI → "
            "AI Case Officer"
        ),
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — allow React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount all routers
    app.include_router(health_router)
    app.include_router(detection_router)
    app.include_router(accounts_router)
    app.include_router(transactions_router)
    app.include_router(ingestion_router)
    app.include_router(investigations_router)

    @app.on_event("startup")
    async def _startup() -> None:
        logger.info("Abhedya-Chakra API v2.0 started — all modules loaded")

    return app


app = create_app()
