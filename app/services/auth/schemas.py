import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, numbers, underscores, hyphens")
        if len(v) < 3 or len(v) > 50:
            raise ValueError("Username must be 3–50 characters")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyCreatedResponse(BaseModel):
    id: uuid.UUID
    name: str
    key: str
    created_at: datetime


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
