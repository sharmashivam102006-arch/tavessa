"""JWT access tokens + opaque refresh tokens (Stage 7).

- Access token: JWT (python-jose, HS256, ``SECRET_KEY``), 15-minute
  expiry. Claims: ``sub`` (user id), ``restaurant_id``,
  ``branch_id``, ``role``, ``jti``.
- Refresh token: ``secrets.token_urlsafe(48)`` — an opaque random
  secret. Only its SHA-256 hash is stored (``RefreshSession``).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings

REFRESH_TOKEN_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    branch_id: uuid.UUID | None,
    role: str,
) -> str:
    now = _now()
    payload = {
        "sub": str(user_id),
        "restaurant_id": str(restaurant_id),
        "branch_id": str(branch_id) if branch_id else None,
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """Return the claims, or None when the token is invalid/expired.
    The caller still loads the user from the DB and checks
    ``is_active`` — a token alone never authorises."""
    try:
        return jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_ws_ticket(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return _now() + timedelta(days=REFRESH_TOKEN_DAYS)
