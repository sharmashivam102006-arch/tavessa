"""Audit logging (Stage 7).

``write_audit`` appends one row in the caller's transaction — the
row commits (or rolls back) with the action it records, so a failed
operation never leaves a false success record. Login failures are
the one exception: they are recorded as failures in their own
transaction (see ``app/auth/service.py``).

Safety: ``details`` is scrubbed — any key that could carry a secret
(password, token, dev key, hash, secret, otp, cookie) is dropped,
and values are truncated. No audit row ever contains a password,
token, or dev key.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.models import AuditLog

logger = logging.getLogger(__name__)

_FORBIDDEN_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "hash",
    "otp",
    "cookie",
    "dev_key",
    "devkey",
    "authorization",
)


def _scrub(details: dict | None) -> dict | None:
    if not details:
        return None
    out: dict = {}
    for key, value in details.items():
        lowered = str(key).lower()
        if any(bad in lowered for bad in _FORBIDDEN_SUBSTRINGS):
            continue
        text = value if isinstance(value, (str, int, float, bool)) else str(value)
        if isinstance(text, str) and len(text) > 500:
            text = text[:500]
        out[str(key)] = text
    return out or None


def write_audit(
    db: Session,
    *,
    restaurant_id: UUID,
    action: str,
    actor_user_id: UUID | None = None,
    branch_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    details: dict | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    """Append an audit row (no commit — the caller's transaction
    owns it). There is deliberately no update/delete API for
    audit rows anywhere in the app."""
    row = AuditLog(
        restaurant_id=restaurant_id,
        branch_id=branch_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=_scrub(details),
        correlation_id=correlation_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row
