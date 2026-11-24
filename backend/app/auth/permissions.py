"""Role / permission matrix — Stage 7 (brief § 2).

Owner has every capability. Manager has a fixed base set plus four
"Configurable" capabilities gated by explicit boolean flags on the
user record (``can_manage_staff``, ``can_manage_settings``,
``can_refund``, ``can_void``). Waiter and kitchen have narrow sets.
``view_kitchen`` for waiter is "Optional" in the brief and is No in
v1 (recorded in the roadmap).

Frontend route-hiding is display-only; every protected endpoint
validates server-side via ``require_permission`` (``app/auth/deps.py``).
"""
import uuid as _uuid

from app.auth.models import User

# capability -> roles that always have it (before flag checks)
_BASE: dict[str, set[str]] = {
    "view_dashboard": {"owner", "manager"},
    "manage_settings": {"owner"},
    "manage_menu": {"owner", "manager"},
    "manage_staff": {"owner"},
    "view_financials": {"owner", "manager"},
    "confirm_cash": {"owner", "manager", "waiter"},
    "serve_orders": {"owner", "manager", "waiter"},
    "update_kitchen": {"owner", "manager", "kitchen"},
    "view_kitchen": {"owner", "manager", "kitchen"},
    "configure_tax": {"owner"},
    "issue_refunds": {"owner"},
    "void_bills": {"owner"},
}

# Manager flag that gates a "Configurable" capability.
_FLAG_FOR: dict[str, str] = {
    "manage_settings": "can_manage_settings",
    "manage_staff": "can_manage_staff",
    "issue_refunds": "can_refund",
    "void_bills": "can_void",
}

CAPABILITIES = tuple(sorted(_BASE))


def has_permission(user: User, capability: str) -> bool:
    """True iff ``user`` may perform ``capability``.

    Owner: always True. Manager: base set, plus the flagged
    capabilities when the matching flag is set. Waiter/kitchen:
    their narrow base set only. Inactive users have no permissions
    (``get_current_user`` already rejects them with 401).
    """
    if not user.is_active:
        return False
    if user.role == "owner":
        return capability in _BASE
    roles = _BASE.get(capability)
    if roles is None:
        return False
    if user.role in roles:
        return True
    if user.role == "manager":
        flag = _FLAG_FOR.get(capability)
        if flag is not None and getattr(user, flag, False):
            return True
    return False


def permission_matrix() -> dict[str, dict[str, str]]:
    """The brief § 2 matrix as implemented, for docs/tests."""
    out: dict[str, dict[str, str]] = {}
    for cap in CAPABILITIES:
        row: dict[str, str] = {}
        for role in ("owner", "manager", "waiter", "kitchen"):
            probe = User(
                restaurant_id=_uuid.UUID(int=0),
                email="probe@example.invalid",
                password_hash="x",
                role=role,
                is_active=True,
            )
            if role == "manager" and cap in _FLAG_FOR:
                row[role] = "Configurable"
            else:
                row[role] = "Yes" if has_permission(probe, cap) else "No"
        out[cap] = row
    return out
