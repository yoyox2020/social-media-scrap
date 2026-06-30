"""
Schemas untuk YouTube pipeline service.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeCollectRequest(BaseModel):
    keyword_id: uuid.UUID
    max_pages: int = Field(default=1, ge=1, le=5)
    max_comment_pages: int = Field(default=1, ge=1, le=5)
    max_comments_per_video: int = Field(default=50, ge=10, le=200)


class SmartSearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=200, description="Kata kunci pencarian YouTube")
    max_pages: int = Field(default=1, ge=1, le=5, description="Jumlah halaman video (~20 per halaman)")
    max_comments_per_video: int = Field(default=20, ge=10, le=100, description="Maks komentar per video")
    max_comment_pages: int = Field(default=1, ge=1, le=5, description="Maks halaman komentar per video")
    force_refresh: bool = Field(default=False, description="Paksa crawl ulang meski data sudah ada di DB")


class DateSearchRequest(BaseModel):
    date_from: date = Field(..., description="Tanggal mulai (YYYY-MM-DD), inklusif")
    date_to: date = Field(..., description="Tanggal akhir (YYYY-MM-DD), inklusif")
    q: str | None = Field(default=None, max_length=200, description="Filter nama keyword (ILIKE, opsional)")
    keyword_id: uuid.UUID | None = Field(default=None, description="Filter per keyword ID (lebih presisi dari q)")
    sort_by: str = Field(default="newest", pattern="^(newest|oldest|views)$", description="newest | oldest | views")
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    include_sentiment: bool = Field(default=True, description="Sertakan distribusi sentimen & breakdown per hari")
    auto_crawl: bool = Field(default=True, description="Jika data belum ada di DB, crawl otomatis dari YouTube (butuh q)")

    @model_validator(mode="after")
    def validate_dates(self) -> "DateSearchRequest":
        if self.date_from > self.date_to:
            raise ValueError("date_from tidak boleh lebih besar dari date_to")
        return self


class TrendingFetchRequest(BaseModel):
    geo: str = Field(default="ID", max_length=10)
    period: str = Field(default="24h", pattern="^(4h|24h|48h|7d)$")
    limit: int = Field(default=10, ge=1, le=25)
    project_id: uuid.UUID
    auto_collect: bool = True
    max_pages_per_keyword: int = Field(default=2, ge=1, le=5)


class YouTubePopularRequest(BaseModel):
    region_code: str = Field(default="ID", max_length=10, description="Kode negara (ISO 3166-1 alpha-2), misal: ID, US, JP")
    limit: int = Field(default=20, ge=1, le=50, description="Jumlah video (maks 50)")
    category_id: str | None = Field(default=None, description="ID kategori YouTube (opsional, misal: '10' untuk musik)")
    save_to_db: bool = Field(default=True, description="Simpan hasil ke DB sebagai Posts")


class ViralSearchRequest(BaseModel):
    keyword_id: uuid.UUID | None = Field(default=None, description="Filter per keyword ID (opsional)")
    q: str | None = Field(default=None, max_length=200, description="Filter nama keyword (ILIKE, opsional)")
    date_from: date | None = Field(default=None, description="Filter dari tanggal publish video (YYYY-MM-DD)")
    date_to: date | None = Field(default=None, description="Filter sampai tanggal publish video (YYYY-MM-DD)")
    sort_by: str = Field(default="views", pattern="^(views|newest|oldest)$", description="views | newest | oldest")
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    auto_search: bool = Field(default=True, description="Jika tidak ada di DB, otomatis cari ke YouTube Data API v3")


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
