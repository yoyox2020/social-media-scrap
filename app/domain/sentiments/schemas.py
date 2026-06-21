import uuid
from datetime import datetime

from pydantic import BaseModel


class SentimentResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID | None
    comment_id: uuid.UUID | None
    label: str
    score: float
    model_version: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
