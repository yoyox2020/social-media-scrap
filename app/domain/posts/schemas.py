import uuid
from datetime import datetime

from pydantic import BaseModel


class PostResponse(BaseModel):
    id: uuid.UUID
    keyword_id: uuid.UUID | None
    external_id: str
    platform: str
    content: str | None
    author: str | None
    url: str | None
    published_at: datetime | None
    collected_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
