import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str


class AdminUserCreate(BaseModel):
    """POST /users -- admin bikin akun user baru, boleh langsung set role/is_active
    (beda dgn UserCreate/register publik yg selalu role="user" default)."""
    email: EmailStr
    username: str
    password: str = Field(..., min_length=8)
    role: str = "user"
    is_active: bool = True


class UserUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None


class AdminUserUpdate(BaseModel):
    """PATCH /users/{id} -- admin edit user lain, termasuk role/status/superuser
    (beda dgn UserUpdate yg cuma username/email, dipakai profil sendiri nanti)."""
    username: str | None = None
    email: EmailStr | None = None
    role: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


class ChangePasswordRequest(BaseModel):
    """PATCH /users/{id}/password -- admin reset password user lain, TIDAK perlu
    password lama (beda dgn ganti password sendiri yg butuh verifikasi lama)."""
    new_password: str = Field(..., min_length=8)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    role: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
