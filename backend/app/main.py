"""FastAPI application entry point.

Run from ``backend/``:
    uvicorn app.main:app --reload --port 8000
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import error_body, register_error_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import limiter

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
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_exceeded(_request: Request, _exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content=error_body(
                "rate_limited", "Too many requests. Please try again shortly."
            ),
        )

    app.include_router(api_router)

    @app.get("/", tags=["health"])
    def root():
        return {"service": "tavessa", "status": "ok"}

    return app


app = create_app()
logger.info("Tavessa backend starting (environment=%s)", settings.environment)
