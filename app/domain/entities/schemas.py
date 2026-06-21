import uuid
from datetime import datetime

from pydantic import BaseModel


class EntityResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID | None
    comment_id: uuid.UUID | None
    text: str
    entity_type: str
    score: float | None
    created_at: datetime

    model_config = {"from_attributes": True}
