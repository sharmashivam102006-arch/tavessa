"""Help-call service (api-plan.md § 5).

One open/acknowledged call per session — a second one is a 409 that
returns the existing call's id. Status starts 'open'; nothing here
claims a waiter has accepted it (acceptance is a staff action, Stage 5).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import HelpCall
from app.schemas.guest import (
    HelpCallDataResponse,
    HelpCallListResponse,
    HelpCallOut,
    HelpCallRequest,
)
from app.services.table_service import GuestContext, _now


def create_help_call(
    db: Session, ctx: GuestContext, request: HelpCallRequest
) -> HelpCallDataResponse:
    existing = (
        db.execute(
            select(HelpCall).where(
                HelpCall.table_session_id == ctx.session.id,
                HelpCall.status.in_(("open", "acknowledged")),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise ApiError(
            409,
            "conflict",
            "There is already an open help call for this table.",
            {
                "help_call_id": str(existing.id),
                "existing_help_call_id": str(existing.id),
                "status": existing.status,
            },
        )
    call = HelpCall(
        restaurant_id=ctx.restaurant.id,
        table_session_id=ctx.session.id,
        table_id=ctx.session.table_id,
        type=request.type,
        note=request.note,
        status="open",
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return HelpCallDataResponse(
        data=HelpCallOut(
            id=call.id,
            table_session_id=call.table_session_id,
            type=call.type,
            note=call.note,
            status=call.status,
            created_at=call.created_at,
        )
    )


def list_help_calls(db: Session, ctx: GuestContext) -> HelpCallListResponse:
    """GET /guest/help-calls — the session's help calls, newest
    first. The guest's REST source of truth for assistance status:
    the Help page refetches this after a socket reconnect (Stage 8).
    Another session's calls are never visible (filtered by session)."""
    calls = (
        db.execute(
            select(HelpCall)
            .where(HelpCall.table_session_id == ctx.session.id)
            .order_by(HelpCall.created_at.desc())
        )
        .scalars()
        .all()
    )
    return HelpCallListResponse(
        data=[
            HelpCallOut(
                id=call.id,
                table_session_id=call.table_session_id,
                type=call.type,
                note=call.note,
                status=call.status,
                created_at=call.created_at,
            )
            for call in calls
        ]
    )
