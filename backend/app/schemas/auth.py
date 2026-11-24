"""Auth + admin schemas (Stage 7). Wire format is camelCase."""
from datetime import datetime
from uuid import UUID

import re

from pydantic import Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value.strip()):
        raise ValueError("must be a valid email address")
    return value.strip().lower()


from app.schemas.common import CamelModel, Pagination


class LoginRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    email: str
    password: str = Field(min_length=1, max_length=200)

    _check_email = field_validator("email", mode="before")(_validate_email)


class UserOut(CamelModel):
    id: UUID
    email: str
    role: str
    restaurant_id: UUID
    branch_id: UUID | None = None
    is_active: bool
    can_manage_staff: bool = False
    can_manage_settings: bool = False
    can_refund: bool = False
    can_void: bool = False
    must_change_password: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class LoginResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginResponseEnvelope(CamelModel):
    data: LoginResponse


class MeResponse(CamelModel):
    data: UserOut


class ChangePasswordRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class WsTicketRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    channel: str

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, value: str) -> str:
        if value not in ("kitchen", "staff"):
            raise ValueError("channel must be 'kitchen' or 'staff'")
        return value


class WsTicketOut(CamelModel):
    ticket: str
    channel: str
    expires_at: datetime


class WsTicketResponse(CamelModel):
    data: WsTicketOut


# --- User management ------------------------------------------------------

class UserCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    email: str
    password: str = Field(min_length=8, max_length=200)

    _check_email = field_validator("email", mode="before")(_validate_email)
    role: str
    branch_id: UUID | None = None
    can_manage_staff: bool = False
    can_manage_settings: bool = False
    can_refund: bool = False
    can_void: bool = False

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        if value not in ("owner", "manager", "waiter", "kitchen"):
            raise ValueError("role must be owner, manager, waiter or kitchen")
        return value


class UserUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    role: str | None = None
    branch_id: UUID | None = None
    is_active: bool | None = None
    can_manage_staff: bool | None = None
    can_manage_settings: bool | None = None
    can_refund: bool | None = None
    can_void: bool | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str | None) -> str | None:
        if value is not None and value not in ("owner", "manager", "waiter", "kitchen"):
            raise ValueError("role must be owner, manager, waiter or kitchen")
        return value


class UserResetPasswordRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    password: str = Field(min_length=8, max_length=200)


class UserListResponse(CamelModel):
    data: list[UserOut]
    pagination: Pagination


class UserDataResponse(CamelModel):
    data: UserOut
