"""Kitchen WebSocket — GET /ws/kitchen (api-plan.md § 9, Stage 4).

The kitchen channel arrives in Stage 4 (not Stage 8): the KDS
subscribes here and re-fetches ``GET /kitchen/orders`` on connect,
reconnect, and on each event. Events are hints only — the database
is the source of truth (architecture.md § 6.4). No business action
is ever performed from a WebSocket message: inbound messages are
ignored.

Auth at connect (dev-only, same key as the kitchen REST routes):
``?restaurant_id=<uuid>&dev_key=<key>``. The key is validated and
then discarded — it is never logged. Failures close the socket
before accept: 4401 bad/missing dev key, 4404 unknown restaurant,
4403 kitchen auth not configured (production). A missing
``restaurant_id`` resolves like the REST rule (exactly one
restaurant -> use it; otherwise the socket is closed, 4400).

This router is mounted WITHOUT the /api/v1 prefix (see main.py).
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.core.errors import ApiError
from app.realtime.manager import manager
from app.services import kitchen_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/kitchen")
async def kitchen_websocket(
    websocket: WebSocket,
    restaurant_id: UUID | None = Query(default=None),
    dev_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # Production: the dev key does not exist as an auth mechanism.
    if settings.is_production:
        await websocket.close(code=4403, reason="kitchen_auth_not_configured")
        return
    # Dev key check — constant-time compare; the key is never logged.
    import secrets

    if not dev_key or not secrets.compare_digest(dev_key, settings.kitchen_dev_key):
        await websocket.close(code=4401, reason="unauthenticated")
        return
    # Restaurant check BEFORE accept — an unknown restaurant is 4404.
    try:
        restaurant = kitchen_service.resolve_restaurant(db, restaurant_id)
    except ApiError as exc:
        code = 4404 if exc.status_code == 404 else 4400
        await websocket.close(code=code, reason=exc.message)
        return

    await websocket.accept()
    await manager.connect(restaurant.id, websocket)
    try:
        while True:
            # Inbound messages carry no business action; they only
            # keep the socket alive (clients may send pings).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Kitchen websocket error")
    finally:
        manager.disconnect(restaurant.id, websocket)
