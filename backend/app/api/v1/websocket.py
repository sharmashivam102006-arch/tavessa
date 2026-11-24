"""Kitchen + Staff WebSockets — GET /ws/kitchen, GET /ws/staff
(api-plan.md § 9, Stages 4–5, Stage 7 ticket auth).

Auth at connect (Stage 7):
- Production: ``?ticket=<single-use ticket>`` only (issued via
  POST /api/v1/ws/tickets). The ticket is validated against the
  DB (hash lookup, not expired, not used, user active + still has
  the channel permission, restaurant matches) and marked used.
  Dev keys are NEVER accepted in production.
- Non-production: a ticket works too; ``?dev_key=`` still works
  (existing tests keep passing unchanged).
- The ticket is never logged. Failures are refused with a close
  code: 4401 bad/missing ticket or dev key, 4404 unknown
  restaurant, 4403 auth not configured (production, no ticket).
  The socket is accepted first and then closed with the code —
  closing before accept would make uvicorn answer the handshake
  with a bare HTTP 403 and the code would never reach the client.

Both channels share the SAME in-process ConnectionManager, keyed
by restaurant (single-process limitation — architecture.md § 6.3).
Events are hints only; receivers re-fetch via REST.
"""
import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.auth.deps import validate_ws_ticket
from app.core.config import settings
from app.core.deps import get_db
from app.core.errors import ApiError
from app.realtime.manager import manager
from app.services import kitchen_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authenticate(
    websocket: WebSocket,
    channel: str,
    restaurant_id: UUID | None,
    ticket: str | None,
    dev_key: str | None,
    expected_dev_key: str,
    db: Session,
) -> tuple[UUID | None, int | None, str | None]:
    """Validate connect-time auth.

    Returns (restaurant id, None, None) on success, else
    (None, close code, reason). The caller accepts the socket and
    then closes it with the code — closing before accept would make
    uvicorn answer the handshake with a bare HTTP 403 and the code
    would never reach the client.
    """
    if ticket:
        try:
            _user, ticket_restaurant = validate_ws_ticket(db, ticket, channel)
        except ApiError as exc:
            code = 4403 if exc.status_code == 403 else 4401
            return None, code, exc.message
        if restaurant_id is not None and restaurant_id != ticket_restaurant:
            return None, 4404, "Restaurant not found."
        return ticket_restaurant, None, None
    # No ticket: production never accepts a dev key.
    if settings.is_production:
        return None, 4403, "websocket_auth_not_configured"
    if not dev_key or not secrets.compare_digest(dev_key, expected_dev_key):
        return None, 4401, "unauthenticated"
    try:
        restaurant = kitchen_service.resolve_restaurant(db, restaurant_id)
    except ApiError as exc:
        code = 4404 if exc.status_code == 404 else 4400
        return None, code, exc.message
    return restaurant.id, None, None


@router.websocket("/ws/kitchen")
async def kitchen_websocket(
    websocket: WebSocket,
    restaurant_id: UUID | None = Query(default=None),
    ticket: str | None = Query(default=None),
    dev_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rid, code, reason = await _authenticate(
        websocket, "kitchen", restaurant_id, ticket, dev_key,
        settings.kitchen_dev_key, db,
    )
    if rid is None:
        await websocket.accept()
        await websocket.close(code=code or 4401, reason=reason or "unauthenticated")
        return
    await websocket.accept()
    await manager.connect(rid, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Kitchen websocket error")
    finally:
        manager.disconnect(rid, websocket)


@router.websocket("/ws/staff")
async def staff_websocket(
    websocket: WebSocket,
    restaurant_id: UUID | None = Query(default=None),
    ticket: str | None = Query(default=None),
    dev_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rid, code, reason = await _authenticate(
        websocket, "staff", restaurant_id, ticket, dev_key,
        settings.staff_dev_key, db,
    )
    if rid is None:
        await websocket.accept()
        await websocket.close(code=code or 4401, reason=reason or "unauthenticated")
        return
    await websocket.accept()
    await manager.connect(rid, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Staff websocket error")
    finally:
        manager.disconnect(rid, websocket)
