"""API v1 router aggregation.

Business routers land stage by stage; each one is included here and
nothing else imports them directly.
"""
from fastapi import APIRouter

from app.api.v1 import guest, health, orders

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(guest.router)
api_router.include_router(orders.router)
