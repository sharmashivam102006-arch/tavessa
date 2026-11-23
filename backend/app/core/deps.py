"""Shared FastAPI dependencies.

Stage 1: only ``get_db`` (re-exported from ``app.db.session``) so routes
have one import location. Stage 3 adds the guest-session dependency;
staff auth (``current_user``, ``require_role``) lands with JWT login in
a later stage — there is no staff auth in Stage 3.

Stage 4 adds ``require_kitchen_dev_access``: a DEV-ONLY gate for the
Kitchen Display System. The KDS has no staff auth yet, so in
non-production environments the kitchen endpoints accept a shared dev
key (``KITCHEN_DEV_KEY``) in the ``X-Kitchen-Dev-Key`` header. In
production the kitchen routes refuse to serve at all (503
``kitchen_auth_not_configured``) until staff auth lands. This gate
does not touch guest token security — guest endpoints are unchanged.
"""
import secrets

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.db.session import get_db
from app.services.table_service import GuestContext, resolve_guest_context

__all__ = ["get_db", "get_guest_context", "require_kitchen_dev_access"]


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


def require_kitchen_dev_access(
    x_kitchen_dev_key: str | None = Header(default=None, alias="X-Kitchen-Dev-Key"),
) -> None:
    """Dev-only kitchen gate (Stage 4).

    Production -> 503: kitchen access requires staff auth, which does
    not exist yet — the KDS is development-access only until then.
    Otherwise the ``X-Kitchen-Dev-Key`` header must equal the
    configured ``KITCHEN_DEV_KEY`` (missing -> 401, wrong -> 403).
    The key is compared in constant time and never logged.
    """
    if settings.is_production:
        raise ApiError(
            503,
            "kitchen_auth_not_configured",
            "Kitchen access requires staff authentication, which is not "
            "configured yet. The Kitchen Display System is "
            "development-access only until staff auth lands.",
        )
    if not x_kitchen_dev_key:
        raise ApiError(
            401, "unauthenticated", "X-Kitchen-Dev-Key header is required."
        )
    if not secrets.compare_digest(x_kitchen_dev_key, settings.kitchen_dev_key):
        raise ApiError(
            403, "forbidden", "The kitchen dev key is not valid for this server."
        )
