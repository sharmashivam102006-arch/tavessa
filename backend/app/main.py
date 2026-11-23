"""FastAPI application entry point.

Run from ``backend/``:
    uvicorn app.main:app --reload --port 8000
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings.validate_for_startup()
    app = FastAPI(
        title="Tavessa",
        description="Restaurant management platform — Guest, Staff, Kitchen, Owner. Cash only in v1.",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(api_router)

    @app.get("/", tags=["health"])
    def root():
        return {"service": "tavessa", "status": "ok"}

    return app


app = create_app()
logger.info("Tavessa backend starting (environment=%s)", settings.environment)
