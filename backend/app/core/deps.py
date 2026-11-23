"""Shared FastAPI dependencies.

Stage 1: only ``get_db`` (re-exported from ``app.db.session``) so routes
have one import location. Auth dependencies (``current_user``,
``require_role``, guest session) land in Stage 3 with auth.
"""
from app.db.session import get_db

__all__ = ["get_db"]
