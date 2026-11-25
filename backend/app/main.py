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
from app.api.v1.websocket import router as websocket_router
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
    # Security headers (Stage 9). No Content-Security-Policy is set
    # here on purpose: the Vite frontend serves inline assets that a
    # CSP would break, and a CSP for the API alone buys nothing — the
    # API serves JSON, not HTML. HSTS is likewise NOT set here: there
    # is no TLS termination in the sandbox/dev setup, so HSTS would
    # be a lie; it is a Stage 10 deployment decision (reverse proxy).
    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # No flow in this app depends on the Referer header (auth is
        # Bearer/cookie + token based), so no-referrer is safe.
        response.headers["Referrer-Policy"] = "no-referrer"
        # API responses carry live business state — never cacheable.
        # Static frontend assets are served elsewhere and untouched.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        # Starlette's CORSMiddleware sends
        # Access-Control-Allow-Credentials: true even when the Origin
        # is NOT on the allowlist (it sets it whenever
        # allow_credentials=True). Without an ACAO header a browser
        # will not expose the response cross-origin anyway, so this
        # is not exploitable — but a credentials header with no
        # allowlisted origin is misleading, so strip it unless an
        # ACAO header was actually set for this origin.
        if "access-control-allow-origin" not in response.headers:
            if "access-control-allow-credentials" in response.headers:
                del response.headers["access-control-allow-credentials"]
        return response
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
    # WebSocket routes mount WITHOUT the /api/v1 prefix:
    # GET /ws/kitchen (api-plan.md § 9).
    app.include_router(websocket_router)

    @app.get("/", tags=["health"])
    def root():
        return {"service": "tavessa", "status": "ok"}

    return app


app = create_app()
logger.info("Tavessa backend starting (environment=%s)", settings.environment)
