"""API v1 router aggregation.

Business routers land stage by stage; each one is included here and
nothing else imports them directly.
"""
from fastapi import APIRouter

from app.api.v1 import admin, guest, health, kitchen, orders, staff
from app.auth.router import router as auth_router
from app.auth.router import ticket_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(guest.router)
api_router.include_router(orders.router)
api_router.include_router(kitchen.router)
api_router.include_router(staff.router)
api_router.include_router(auth_router)
api_router.include_router(admin.router)
# WS ticket issuance lives under /api/v1/ws (see auth/router.py).
api_router.include_router(ticket_router)
