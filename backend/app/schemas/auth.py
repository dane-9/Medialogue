from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from .common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class BootstrapStatusResponse(BaseModel):
    required: bool


class AuthRuntimeConfigResponse(BaseModel):
    csrf_cookie_name: str


class BootstrapRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    authenticated: bool = True
    csrf_token: str
    user: "AdminResponse"
    default_password_warning: bool


class AdminResponse(ORMModel):
    id: UUID
    username: str
    is_default_password: bool
    created_at: datetime


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class SecurityResponse(BaseModel):
    default_password_warning: bool
    session_expires_at: datetime | None = None


class AuthSessionResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    current: bool
