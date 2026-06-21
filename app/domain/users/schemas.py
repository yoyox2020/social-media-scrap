import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str


class UserUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
