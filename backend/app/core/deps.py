"""Shared FastAPI dependencies.

Stage 1: only ``get_db`` (re-exported from ``app.db.session``) so routes
have one import location. Stage 3 adds the guest-session dependency;
staff auth (``current_user``, ``require_role``) lands with JWT login in
a later stage — there is no staff auth in Stage 3.
"""
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.session import get_db
from app.services.table_service import GuestContext, resolve_guest_context

__all__ = ["get_db", "get_guest_context"]


def get_guest_context(
    db: Session = Depends(get_db),
    x_table_token: str | None = Header(default=None, alias="X-Table-Token"),
) -> GuestContext:
    """Resolve the ``X-Table-Token`` header to a guest context.

    Missing header -> 401 invalid_table_token. Unknown/revoked token
    and expired sessions are mapped in ``table_service`` (401 / 410).
    Every guest query is then filtered by this context's session.
    """
    if not x_table_token:
        raise ApiError(401, "invalid_table_token", "X-Table-Token header is required.")
    return resolve_guest_context(db, x_table_token)
