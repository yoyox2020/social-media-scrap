import uuid
from datetime import datetime

from pydantic import BaseModel


class KeywordCreate(BaseModel):
    project_id: uuid.UUID
    keyword: str


class KeywordUpdate(BaseModel):
    keyword: str | None = None
    is_active: bool | None = None


class KeywordResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    keyword: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
