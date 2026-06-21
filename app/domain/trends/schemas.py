import uuid
from datetime import datetime

from pydantic import BaseModel


class TrendResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    keyword: str
    platform: str
    post_count: int
    sentiment_score: float | None
    period_start: datetime
    period_end: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
