"""Auth routes — POST /api/v1/auth/* and POST /api/v1/ws/tickets.

- Login is rate-limited (10/minute per IP, slowapi). The suite
  disables the limiter globally (conftest); the rate-limit test
  re-enables it for that test only.
- The refresh token travels as an HttpOnly cookie (SameSite=Lax,
  Secure in production, Path=/api/v1/auth). The access token is
  returned in JSON; the frontend keeps it in memory only, never
  in localStorage.
- No CSRF token (Stage 9 closure — a deliberate decision, not an
  omission). ``/refresh`` is POST-only (GET returns 405) and the
  cookie is SameSite=Lax, so a cross-site POST does not carry the
  cookie at all — a forged refresh arrives cookieless and gets a
  401. Even if a browser ever attached it, the attacker could not
  read the response (no CORS allow-list entry for their origin;
  the new tokens travel as an HttpOnly cookie + JSON the
  same-origin policy hides), so the worst case is a rotation the
  attacker cannot use: the victim's next refresh then fails reuse
  detection, the whole family is revoked, and the victim is
  logged out (a nuisance, not a takeover). The frontend also
  attempts the refresh only on boot and on a 401 retry, never in
  a loop.
- ``/ws/tickets`` issues single-use opaque tickets (60s) for the
  kitchen/staff WebSocket channels.
"""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import service as auth_service
from app.auth.deps import get_current_user
from app.auth.models import User, WsTicket
from app.auth.permissions import has_permission
from app.auth.tokens import hash_ws_ticket
from app.core.config import settings
from app.core.deps import get_db
from app.core.errors import ApiError
from app.core.rate_limit import limiter
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponseEnvelope,
    MeResponse,
    UserOut,
    WsTicketRequest,
    WsTicketResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "tavessa_refresh"
LOGIN_LIMIT = "10/minute"
WS_TICKET_TTL_SECONDS = 60


def _refresh_cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.is_production,
        "path": "/api/v1/auth",
    }


def _login_response(payload: dict, raw_refresh: str, status: int = 200) -> JSONResponse:
    response = JSONResponse(content={"data": payload}, status_code=status)
    response.set_cookie(REFRESH_COOKIE, raw_refresh, **_refresh_cookie_kwargs())
    return response


def _clear_refresh_cookie(response: JSONResponse) -> JSONResponse:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return response


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/login")
@limiter.limit(LOGIN_LIMIT)
async def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    payload, raw_refresh = auth_service.login(
        db, body.email, body.password, _client_ip(request), _user_agent(request)
    )
    return _login_response(payload, raw_refresh)


@router.post("/refresh")
async def refresh(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise ApiError(401, "unauthenticated", "No refresh session found.")
    payload, new_raw = auth_service.refresh(
        db, raw, _client_ip(request), _user_agent(request)
    )
    return _login_response(payload, new_raw)


@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    auth_service.logout(db, request.cookies.get(REFRESH_COOKIE))
    return _clear_refresh_cookie(JSONResponse(content={"data": {"loggedOut": True}}))


@router.post("/change-password", response_model=MeResponse)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    auth_service.change_password(db, user, body.current_password, body.new_password)
    db.refresh(user)
    return MeResponse(data=UserOut.model_validate(user, from_attributes=True))


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse(data=UserOut.model_validate(user, from_attributes=True))


# --- WebSocket tickets ------------------------------------------------------

ticket_router = APIRouter(prefix="/ws", tags=["websocket-auth"])


@ticket_router.post("/tickets", response_model=WsTicketResponse)
def issue_ws_ticket(
    body: WsTicketRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Issue a single-use WS ticket for ``channel``. The user's role
    / permission for the channel is validated here: kitchen needs
    view_kitchen or update_kitchen; staff needs serve_orders,
    confirm_cash or view_dashboard."""
    if body.channel == "kitchen":
        allowed = has_permission(user, "view_kitchen") or has_permission(
            user, "update_kitchen"
        )
    else:
        allowed = (
            has_permission(user, "serve_orders")
            or has_permission(user, "confirm_cash")
            or has_permission(user, "view_dashboard")
        )
    if not allowed:
        raise ApiError(
            403, "forbidden", "This account cannot use this channel."
        )
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=WS_TICKET_TTL_SECONDS)
    db.add(
        WsTicket(
            user_id=user.id,
            restaurant_id=user.restaurant_id,
            channel=body.channel,
            ticket_hash=hash_ws_ticket(raw),
            expires_at=expires_at,
        )
    )
    db.commit()
    return WsTicketResponse(
        data={"ticket": raw, "channel": body.channel, "expires_at": expires_at}
    )
