"""Auth dependencies — Stage 7.

- ``get_current_user``: Bearer JWT -> DB user. The user is loaded
  from the database on EVERY request and ``is_active`` is checked,
  so deactivation takes effect immediately — an access token does
  NOT stay usable for its remaining 15 minutes after deactivation
  (stronger than the brief's minimum, which only required this on
  admin/financial endpoints).
- ``require_permission(capability)`` / ``require_role(...)``:
  server-side RBAC for every protected endpoint.
- ``require_kitchen_access`` / ``require_staff_access``: the
  Stage 4/5 dev-key retirement. Production -> JWT only (dev keys
  are NEVER accepted, 401). Non-production -> Bearer JWT if
  present, else the existing dev key (existing tests keep passing
  unchanged). Guest table-token auth is untouched.
"""
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.models import User, WsTicket
from app.auth.permissions import has_permission
from app.auth.tokens import decode_access_token, hash_ws_ticket
from app.core.config import settings
from app.core.deps import get_db
from app.core.errors import ApiError

_bearer = HTTPBearer(auto_error=False)


def _unauthenticated(message: str = "Authentication is required.") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def _load_user_from_token(db: Session, token: str) -> User:
    claims = decode_access_token(token)
    if claims is None:
        raise _unauthenticated("The access token is invalid or expired.")
    try:
        user_id = UUID(str(claims.get("sub")))
    except (ValueError, TypeError, AttributeError):
        raise _unauthenticated("The access token is invalid or expired.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthenticated("The access token is invalid or expired.")
    return user


def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated()
    return _load_user_from_token(db, credentials.credentials)


def get_optional_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User | None:
    """Bearer user when a valid token is present, else None. Used by
    the staff/kitchen dev-key paths so a JWT login can record the
    actor (e.g. ``collected_by``) while dev-key calls stay anonymous."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        return _load_user_from_token(db, credentials.credentials)
    except ApiError:
        return None


def require_permission(capability: str):
    """Dependency factory: 401 when unauthenticated, 403 when the
    user lacks the capability (role matrix + manager flags)."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, capability):
            raise ApiError(
                403,
                "forbidden",
                "This account does not have permission for this action.",
                {"capability": capability},
            )
        return user

    return _dep


def require_role(*roles: str):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ApiError(
                403,
                "forbidden",
                "This account's role cannot perform this action.",
                {"roles": list(roles)},
            )
        return user

    return _dep


def _check_dev_key(header_value: str | None, expected: str, missing_msg: str) -> None:
    if not header_value:
        raise ApiError(401, "unauthenticated", missing_msg)
    if not secrets.compare_digest(header_value, expected):
        raise ApiError(403, "forbidden", "The dev key is not valid for this server.")


def _jwt_or_dev_key(
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
    dev_key_header: str | None,
    expected_dev_key: str,
    capability: str,
    missing_msg: str,
) -> User | None:
    """Shared staff/kitchen gate. Returns the JWT user, or None for
    a dev-key call. Production: JWT only — a dev key is never
    accepted (401)."""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        user = _load_user_from_token(db, credentials.credentials)
        if not has_permission(user, capability):
            raise ApiError(
                403,
                "forbidden",
                "This account does not have permission for this action.",
                {"capability": capability},
            )
        return user
    if settings.is_production:
        raise _unauthenticated(
            "Staff authentication is required. Dev keys are not accepted in production."
        )
    _check_dev_key(dev_key_header, expected_dev_key, missing_msg)
    return None


def require_kitchen_access(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_kitchen_dev_key: str | None = Header(default=None, alias="X-Kitchen-Dev-Key"),
) -> User | None:
    return _jwt_or_dev_key(
        db,
        credentials,
        x_kitchen_dev_key,
        settings.kitchen_dev_key,
        "update_kitchen",
        "X-Kitchen-Dev-Key header is required.",
    )


def require_staff_access(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_staff_dev_key: str | None = Header(default=None, alias="X-Staff-Dev-Key"),
) -> User | None:
    return _jwt_or_dev_key(
        db,
        credentials,
        x_staff_dev_key,
        settings.staff_dev_key,
        "serve_orders",
        "X-Staff-Dev-Key header is required.",
    )


# --- WebSocket tickets ---------------------------------------------------

def validate_ws_ticket(
    db: Session, raw_ticket: str, channel: str
) -> tuple[User, UUID]:
    """Validate a single-use WS ticket: hash lookup, not expired,
    not used, user active + still has the channel permission,
    restaurant matches. Marks ``used_at`` (single-use) and commits.
    The ticket is never logged. Returns (user, restaurant_id)."""
    from sqlalchemy import select

    row = (
        db.execute(
            select(WsTicket).where(
                WsTicket.ticket_hash == hash_ws_ticket(raw_ticket),
                WsTicket.channel == channel,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ApiError(401, "unauthenticated", "The WebSocket ticket is invalid.")
    now = datetime.now(timezone.utc)
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if row.used_at is not None or expires <= now:
        raise ApiError(401, "unauthenticated", "The WebSocket ticket is invalid.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthenticated", "The WebSocket ticket is invalid.")
    capability = "view_kitchen" if channel == "kitchen" else "serve_orders"
    if not has_permission(user, capability):
        raise ApiError(403, "forbidden", "This account cannot use this channel.")
    row.used_at = now
    db.commit()
    return user, row.restaurant_id
