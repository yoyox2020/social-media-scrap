import uuid
from datetime import datetime

from pydantic import BaseModel


class CommentResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    external_id: str
    content: str | None
    author: str | None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
