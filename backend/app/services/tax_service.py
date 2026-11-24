"""Tax configuration + branch-timezone helpers (Stage 7, D1).

Tax (decision D1, settled in Stage 7 as an owner-configured
policy, NOT a claimed compliance position):
- No config exists -> "unconfigured" (``enabled=False``, no rate).
  This is DISTINCT from an explicit 0% rate (``enabled=True`` +
  ``rate_bps=0``).
- The active config at an instant is the row with the greatest
  ``effective_from`` <= now (restaurant-level, ``branch_id`` NULL).
- While a bill is ``issued`` its totals recompute with the config
  effective NOW on every regeneration. At the freeze point
  (issued -> payment_requested, or at payment when a bill is paid
  without a request) the applicable policy + computed tax are
  snapshotted onto the bill and never change afterwards.
- Formulae (integer paise):
  - exclusive: total = subtotal + tax, tax = subtotal * rate // 10000
  - inclusive: total = subtotal, tax = the tax component inside it
    = subtotal * rate // (10000 + rate)
- Date boundaries for dashboards/reports are computed in the
  BRANCH timezone (``zoneinfo``), never the server timezone.
"""
from datetime import datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import TaxConfiguration
from app.models import Branch

DEFAULT_TIMEZONE = "Asia/Kolkata"


def branch_timezone(db: Session, branch_id: UUID | None) -> ZoneInfo:
    """The branch's timezone, falling back to Asia/Kolkata."""
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if branch is not None and branch.timezone:
            try:
                return ZoneInfo(branch.timezone)
            except ZoneInfoNotFoundError:
                pass
    return ZoneInfo(DEFAULT_TIMEZONE)


def day_bounds_in_tz(tz: ZoneInfo, day: datetime) -> tuple[datetime, datetime]:
    """UTC datetimes bounding one local calendar day."""
    local = day.astimezone(tz)
    start_local = datetime.combine(local.date(), time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def active_tax_config(
    db: Session, restaurant_id: UUID, at: datetime | None = None
) -> TaxConfiguration | None:
    """The config in force at ``at`` (default now): greatest
    ``effective_from`` <= ``at`` among enabled-or-not rows. A row
    with ``enabled=False`` in force means explicitly unconfigured
    (tax off). Returns None only when NO config row exists yet."""
    moment = at or datetime.now(timezone.utc)
    return (
        db.execute(
            select(TaxConfiguration)
            .where(
                TaxConfiguration.restaurant_id == restaurant_id,
                TaxConfiguration.branch_id.is_(None),
                TaxConfiguration.effective_from <= moment,
            )
            .order_by(TaxConfiguration.effective_from.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def compute_tax(
    subtotal_paise: int, config: TaxConfiguration | None
) -> tuple[int, int]:
    """(tax_paise, total_paise) for a subtotal under ``config``.
    Unconfigured / disabled -> (0, subtotal)."""
    if config is None or not config.enabled or config.rate_bps is None:
        return 0, subtotal_paise
    rate = config.rate_bps
    if config.mode == "inclusive":
        tax = subtotal_paise * rate // (10000 + rate)
        return tax, subtotal_paise
    tax = subtotal_paise * rate // 10000
    return tax, subtotal_paise + tax


def tax_snapshot_fields(config: TaxConfiguration | None) -> dict:
    """Snapshot columns for a bill frozen under ``config``. NULLs
    mean tax was unconfigured at freeze."""
    if config is None or not config.enabled:
        return {
            "tax_rate_bps_snapshot": None,
            "tax_mode_snapshot": None,
            "tax_label_snapshot": None,
        }
    return {
        "tax_rate_bps_snapshot": config.rate_bps,
        "tax_mode_snapshot": config.mode,
        "tax_label_snapshot": config.label,
    }
