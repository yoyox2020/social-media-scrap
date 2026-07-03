import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class RelatedAccount(BaseModel):
    platform: str
    username: str


class TrendRecommendationItem(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)
    score: float = Field(..., ge=0.0, le=1.0)
    related_accounts: list[RelatedAccount] = Field(default_factory=list)


class TrendRecommendationBatchCreate(BaseModel):
    items: list[TrendRecommendationItem] = Field(..., min_length=1)
    recommendation_date: date | None = None
    source: str = "external_ai"


class TrendRecommendationResponse(BaseModel):
    id: uuid.UUID
    topic: str
    score: float
    related_accounts: list[RelatedAccount]
    source: str
    recommendation_date: date
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TrendRecommendationSubmitResult(BaseModel):
    created: list[str]
    updated: list[str]
    evicted: list[str]
    rejected: list[str]
