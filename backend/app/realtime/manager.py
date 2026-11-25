"""In-process realtime connection manager (Stage 4, extended Stage 8).

SINGLE-PROCESS ONLY. Connections live in memory in this process:
``dict[restaurant_id, set[WebSocket]]`` for staff/kitchen and
``dict[table_session_id, set[WebSocket]]`` for guests (Stage 8).
There is no Redis broker (architecture.md § 6.3): a second ``api``
replica would not receive events published here, and a socket
connected to another replica would not receive events published by
this one. That is acceptable for v1 (one replica) and is the
documented trigger for adding Redis behind an EventBus interface —
the service code does not change when the broker changes.

Rules enforced here (architecture.md § 3.2):
- Broadcast AFTER the database transaction commits. The manager never
  writes business state and never runs inside a transaction; routes
  call ``broadcast`` / ``broadcast_guest`` only after the service
  has returned successfully.
- Broadcast is best-effort per socket: a dead socket is removed and
  never raises to the caller, so one broken KDS screen cannot fail
  an order placement or a status transition.
- Events carry identifiers + a summary only (api-plan.md § 9.3); the
  receiver re-fetches the full order via REST. The database is the
  source of truth, never the event.
- ``version`` is a per-restaurant (or per-session, for guests)
  monotonically increasing counter held in memory; a client that
  sees a gap re-fetches via REST.
- CHANNEL ISOLATION (Stage 8): a guest socket receives ONLY its
  own session's events (``broadcast_guest``); staff/kitchen sockets
  never receive guest-scoped events and vice versa. The two
  subscription sets are disjoint by construction — there is no
  call that fans a guest event out to a restaurant channel, and
  no call that fans a restaurant event out to guest sockets.
"""
import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConnectionManager:
    """In-process WebSocket fan-out: restaurant-keyed (staff/kitchen)
    and session-keyed (guest, Stage 8)."""

    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = {}
        self._versions: dict[UUID, int] = {}
        self._guest_connections: dict[UUID, set[WebSocket]] = {}
        self._guest_versions: dict[UUID, int] = {}

    async def connect(self, restaurant_id: UUID, websocket: WebSocket) -> None:
        self._connections.setdefault(restaurant_id, set()).add(websocket)

    def disconnect(self, restaurant_id: UUID, websocket: WebSocket) -> None:
        sockets = self._connections.get(restaurant_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._connections[restaurant_id]

    def connection_count(self, restaurant_id: UUID) -> int:
        return len(self._connections.get(restaurant_id, ()))

    async def connect_guest(
        self, table_session_id: UUID, websocket: WebSocket
    ) -> None:
        """Subscribe a guest socket to exactly one table session's
        events. The binding was validated at connect (ticket); the
        socket receives nothing from any other session."""
        self._guest_connections.setdefault(table_session_id, set()).add(websocket)

    def disconnect_guest(
        self, table_session_id: UUID, websocket: WebSocket
    ) -> None:
        sockets = self._guest_connections.get(table_session_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._guest_connections[table_session_id]

    def guest_connection_count(self, table_session_id: UUID) -> int:
        return len(self._guest_connections.get(table_session_id, ()))

    async def broadcast(
        self,
        restaurant_id: UUID,
        event_type: str,
        table_id: UUID | None,
        data: dict,
    ) -> dict:
        """Build the § 9.3 envelope and send it to every socket for
        this restaurant. Returns the envelope. A dead socket is
        removed; broadcast never raises to the caller."""
        version = self._versions.get(restaurant_id, 0) + 1
        self._versions[restaurant_id] = version
        event = {
            "type": event_type,
            "restaurant_id": str(restaurant_id),
            "table_id": str(table_id) if table_id is not None else None,
            "data": data,
            "version": version,
            "occurred_at": _now_iso(),
        }
        sockets = list(self._connections.get(restaurant_id, ()))
        if not sockets:
            return event
        results = await asyncio.gather(
            *(ws.send_json(event) for ws in sockets), return_exceptions=True
        )
        for ws, result in zip(sockets, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Dropping dead kitchen socket for restaurant %s: %s",
                    restaurant_id,
                    result,
                )
                self.disconnect(restaurant_id, ws)
        return event

    async def broadcast_guest(
        self,
        table_session_id: UUID,
        event_type: str,
        restaurant_id: UUID,
        table_id: UUID | None,
        data: dict,
    ) -> dict:
        """Build the § 9.3 envelope and send it ONLY to the guest
        sockets subscribed to ``table_session_id``. Same envelope
        shape as ``broadcast``; ``version`` is per-session. Staff /
        kitchen sockets never receive these events. Returns the
        envelope. A dead socket is removed; broadcast never raises
        to the caller."""
        version = self._guest_versions.get(table_session_id, 0) + 1
        self._guest_versions[table_session_id] = version
        event = {
            "type": event_type,
            "restaurant_id": str(restaurant_id),
            "table_id": str(table_id) if table_id is not None else None,
            "data": data,
            "version": version,
            "occurred_at": _now_iso(),
        }
        sockets = list(self._guest_connections.get(table_session_id, ()))
        if not sockets:
            return event
        results = await asyncio.gather(
            *(ws.send_json(event) for ws in sockets), return_exceptions=True
        )
        for ws, result in zip(sockets, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Dropping dead guest socket for session %s: %s",
                    table_session_id,
                    result,
                )
                self.disconnect_guest(table_session_id, ws)
        return event


# One manager for the process. Routes import this instance; tests may
# import it to assert connection counts.
manager = ConnectionManager()
