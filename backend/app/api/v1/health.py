"""Health check — the only endpoint in Stage 1.

``GET /api/v1/health`` returns ``ok`` only when the database answers
``SELECT 1``. A database outage is reported as ``degraded`` with HTTP
200 (so load balancers / the frontend status check can distinguish
"API up, DB down" from "API down") — the frontend shows this as
"Backend reachable, database unavailable", never a stack trace.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.schemas.common import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        logger.exception("Health check: database unavailable")
        database = "unavailable"
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        environment=settings.environment,
        version="0.1.0",
    )
