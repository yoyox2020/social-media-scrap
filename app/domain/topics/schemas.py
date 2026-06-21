import uuid
from datetime import datetime

from pydantic import BaseModel


class TopicResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    post_count: int
    created_at: datetime

    model_config = {"from_attributes": True}
