import uuid
from datetime import datetime

from pydantic import BaseModel


class ReportCreate(BaseModel):
    project_id: uuid.UUID
    title: str
    format: str = "json"


class ReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    summary: str | None
    format: str
    file_path: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
