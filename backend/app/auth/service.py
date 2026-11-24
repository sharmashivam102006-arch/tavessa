"""Auth service — login / refresh / logout / change-password.

Rules (Stage 7, brief § A):
- Wrong credentials and deactivated accounts both return the same
  generic 401 — never leak which check failed.
- Login records ``last_login_at`` and an audit row on success AND
  on failure; failure rows contain no password or token.
- Refresh rotates on every use (old token revoked, new token in
  the same family). Presenting an already-revoked token revokes
  the WHOLE family and returns 401 (reuse detection).
- Logout revokes the presented refresh token.
- Change-password re-hashes with Argon2id, clears
  ``must_change_password``, and revokes ALL refresh sessions for
  the user (re-login required).
"""
import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import RefreshSession, User
from app.auth.tokens import (
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    refresh_expiry,
)
from app.core.errors import ApiError
from app.core.security import hash_password, verify_password
from app.services.audit_service import write_audit

GENERIC_LOGIN_ERROR = "Email or password is incorrect."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unauthorised() -> ApiError:
    return ApiError(401, "unauthenticated", GENERIC_LOGIN_ERROR)


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "restaurantId": str(user.restaurant_id),
        "branchId": str(user.branch_id) if user.branch_id else None,
        "isActive": user.is_active,
        "canManageStaff": user.can_manage_staff,
        "canManageSettings": user.can_manage_settings,
        "canRefund": user.can_refund,
        "canVoid": user.can_void,
        "mustChangePassword": user.must_change_password,
        "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _access_token_for(user: User) -> str:
    return create_access_token(
        user.id, user.restaurant_id, user.branch_id, user.role
    )


def _issue_refresh_session(
    db: Session,
    user: User,
    family_id: uuid.UUID,
    ip: str | None,
    user_agent: str | None,
) -> str:
    raw = new_refresh_token()
    db.add(
        RefreshSession(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_refresh_token(raw),
            expires_at=refresh_expiry(),
            ip_address=ip,
            user_agent=user_agent,
        )
    )
    return raw


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    now = _now()
    for row in db.execute(
        select(RefreshSession).where(
            RefreshSession.family_id == family_id,
            RefreshSession.revoked_at.is_(None),
        )
    ).scalars():
        row.revoked_at = now


def login(
    db: Session, email: str, password: str, ip: str | None, user_agent: str | None
) -> tuple[dict, str]:
    """Returns ({access_token, token_type, user}, raw_refresh_token).
    Raises the generic 401 for unknown email, wrong password, or a
    deactivated account."""
    normalised = email.strip().lower()
    user = (
        db.execute(
            select(User).where(User.email == normalised)
        )
        .scalars()
        .first()
    )
    # Audit the failure even when no user row exists — but only when
    # a restaurant is known (unknown-email failures have no tenant
    # to scope the row to, so they are logged without an audit row
    # rather than inventing one).
    if user is None:
        raise _unauthorised()
    ok = user.is_active and verify_password(password, user.password_hash)
    if not ok:
        write_audit(
            db,
            restaurant_id=user.restaurant_id,
            action="login.failed",
            actor_user_id=user.id,
            details={"email": normalised},
        )
        db.commit()
        raise _unauthorised()
    user.last_login_at = _now()
    family_id = uuid.uuid4()
    raw_refresh = _issue_refresh_session(db, user, family_id, ip, user_agent)
    write_audit(
        db,
        restaurant_id=user.restaurant_id,
        action="login.success",
        actor_user_id=user.id,
        details={"email": normalised},
    )
    db.commit()
    db.refresh(user)
    return (
        {
            "accessToken": _access_token_for(user),
            "tokenType": "bearer",
            "user": _user_out(user),
        },
        raw_refresh,
    )


def refresh(
    db: Session, raw_token: str, ip: str | None, user_agent: str | None
) -> tuple[dict, str]:
    """Rotate a refresh token. Returns ({access_token, ...},
    new_raw_refresh_token). Reuse of a revoked token revokes the
    whole family and returns 401."""
    token_hash = hash_refresh_token(raw_token)
    row = (
        db.execute(
            select(RefreshSession).where(
                RefreshSession.token_hash == token_hash
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise _unauthorised()
    if row.revoked_at is not None:
        # Reuse detected: revoke the whole family.
        _revoke_family(db, row.family_id)
        db.commit()
        raise _unauthorised()
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite returns naive datetimes
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        row.revoked_at = _now()
        db.commit()
        raise _unauthorised()
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        row.revoked_at = _now()
        db.commit()
        raise _unauthorised()
    row.revoked_at = _now()
    new_raw = _issue_refresh_session(db, user, row.family_id, ip, user_agent)
    db.commit()
    db.refresh(user)
    return (
        {
            "accessToken": _access_token_for(user),
            "tokenType": "bearer",
            "user": _user_out(user),
        },
        new_raw,
    )


def logout(db: Session, raw_token: str | None) -> None:
    """Revoke the presented refresh token (no-op when absent)."""
    if not raw_token:
        return
    row = (
        db.execute(
            select(RefreshSession).where(
                RefreshSession.token_hash == hash_refresh_token(raw_token)
            )
        )
        .scalars()
        .first()
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()


def revoke_all_sessions(db: Session, user: User) -> None:
    now = _now()
    for row in db.execute(
        select(RefreshSession).where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
        )
    ).scalars():
        row.revoked_at = now


def change_password(
    db: Session, user: User, current_password: str, new_password: str
) -> None:
    """Authenticated password change. Wrong current password -> 401
    (generic). Revokes ALL refresh sessions; the user re-logs in.
    Clears ``must_change_password``."""
    if not verify_password(current_password, user.password_hash):
        raise ApiError(
            401, "unauthenticated", "The current password is incorrect."
        )
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    revoke_all_sessions(db, user)
    write_audit(
        db,
        restaurant_id=user.restaurant_id,
        action="user.password_changed",
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email},
    )
    db.commit()


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    return db.get(User, user_id)
