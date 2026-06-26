"""
Schemas untuk YouTube pipeline service.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeCollectRequest(BaseModel):
    keyword_id: uuid.UUID
    max_pages: int = Field(default=2, ge=1, le=10)
    max_comment_pages: int = Field(default=3, ge=1, le=10)
    max_comments_per_video: int = Field(default=100, ge=10, le=500)


class TrendingFetchRequest(BaseModel):
    geo: str = Field(default="ID", max_length=10)
    period: str = Field(default="24h", pattern="^(4h|24h|48h|7d)$")
    limit: int = Field(default=10, ge=1, le=25)
    project_id: uuid.UUID
    auto_collect: bool = True
    max_pages_per_keyword: int = Field(default=2, ge=1, le=5)


class DashboardRequest(BaseModel):
    project_id: uuid.UUID | None = None


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Trending
# ─────────────────────────────────────────────────────────────────────────────

class TrendingItemResponse(BaseModel):
    rank: int
    title: str
    traffic: str
    description: str
    published_at: datetime | None


class TrendingFetchResponse(BaseModel):
    geo: str
    period: str
    fetched_at: datetime
    items: list[TrendingItemResponse]
    keywords_created: int
    jobs_queued: int


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Comment Collection
# ─────────────────────────────────────────────────────────────────────────────

class CommentCollectionResult(BaseModel):
    video_external_id: str
    comments_fetched: int = 0
    comments_new: int = 0
    comments_analyzed: int = 0
    errors: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Sentiment
# ─────────────────────────────────────────────────────────────────────────────

class SentimentDistributionItem(BaseModel):
    label: str
    count: int
    percentage: float


class SentimentDistributionResponse(BaseModel):
    keyword_id: uuid.UUID
    keyword_text: str
    total_comments: int
    distribution: list[SentimentDistributionItem]


class SentimentTableRow(BaseModel):
    comment_id: uuid.UUID
    comment_text: str | None
    author: str | None
    video_url: str | None
    matched_positive: list[str]
    matched_negative: list[str]
    removed_stopwords: list[str]
    score: float
    label: str
    analyzed_at: datetime


class SentimentTableResponse(BaseModel):
    keyword_id: uuid.UUID
    keyword_text: str
    total: int
    rows: list[SentimentTableRow]


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Word Cloud
# ─────────────────────────────────────────────────────────────────────────────

class WordCloudItem(BaseModel):
    word: str
    count: int


class WordCloudResponse(BaseModel):
    keyword_id: uuid.UUID
    sentiment_filter: str | None
    words: list[WordCloudItem]


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Dashboard
# ─────────────────────────────────────────────────────────────────────────────

class DashboardSummary(BaseModel):
    total_trending_today: int
    total_keywords: int
    total_videos: int
    total_comments: int
    total_analyzed: int
    last_updated: datetime


class KeywordSentimentSummary(BaseModel):
    keyword_id: uuid.UUID
    keyword_text: str
    total_videos: int
    total_comments: int
    positif: int
    negatif: int
    netral: int
    dominant_sentiment: str


class DashboardResponse(BaseModel):
    summary: DashboardSummary
    sentiment_overview: list[SentimentDistributionItem]
    keyword_summaries: list[KeywordSentimentSummary]
    recent_trending: list[TrendingItemResponse]


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE — Pipeline Status
# ─────────────────────────────────────────────────────────────────────────────

class KeywordPipelineStatus(BaseModel):
    keyword_id: uuid.UUID
    keyword_text: str
    is_active: bool
    total_videos: int
    total_comments: int
    total_analyzed: int
    coverage_pct: float        # % komentar yang sudah dianalisis
    positif: int
    negatif: int
    netral: int
