import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel


# ── Request ───────────────────────────────────────────────────────────────────

class GenerateReportRequest(BaseModel):
    keyword_id: uuid.UUID
    project_id: uuid.UUID
    title: str = ""
    format: str = "json"          # json | pdf | docx
    period: str = "day"           # day | week | month (untuk trend)
    include_posts_sample: bool = True
    posts_sample_size: int = 5


class ReportJobResponse(BaseModel):
    report_id: uuid.UUID
    job_id: str
    status: str = "pending"
    format: str = "json"


# ── Internal data container ────────────────────────────────────────────────────

@dataclass
class SentimentData:
    distribution: dict[str, int] = field(default_factory=dict)
    percentages: dict[str, float] = field(default_factory=dict)
    dominant: str = "neutral"
    total_analyzed: int = 0
    examples: list[dict] = field(default_factory=list)


@dataclass
class EntityData:
    by_type: dict[str, list[dict]] = field(default_factory=dict)
    total_unique: int = 0


@dataclass
class TrendData:
    volume: list[dict] = field(default_factory=list)
    sentiment: list[dict] = field(default_factory=list)
    platform_breakdown: dict[str, int] = field(default_factory=dict)
    direction: str = "stabil"
    total_posts: int = 0


@dataclass
class ReportData:
    """Container data lengkap untuk satu laporan keyword."""

    # Meta
    report_id: uuid.UUID = field(default_factory=uuid.uuid4)
    keyword_id: uuid.UUID = field(default_factory=uuid.uuid4)
    keyword_text: str = ""
    title: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)
    period: str = "day"

    # Post statistics
    total_posts: int = 0
    processed_posts: int = 0
    near_duplicates: int = 0
    language_breakdown: dict[str, int] = field(default_factory=dict)

    # AI results
    sentiment: SentimentData = field(default_factory=SentimentData)
    entities: EntityData = field(default_factory=EntityData)
    trend: TrendData = field(default_factory=TrendData)

    # Sample posts
    top_posts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meta": {
                "report_id": str(self.report_id),
                "keyword_id": str(self.keyword_id),
                "keyword": self.keyword_text,
                "title": self.title,
                "generated_at": self.generated_at.isoformat(),
                "period": self.period,
            },
            "posts": {
                "total": self.total_posts,
                "processed": self.processed_posts,
                "near_duplicates": self.near_duplicates,
                "language_breakdown": self.language_breakdown,
            },
            "sentiment": {
                "total_analyzed": self.sentiment.total_analyzed,
                "distribution": self.sentiment.distribution,
                "percentages": self.sentiment.percentages,
                "dominant": self.sentiment.dominant,
                "examples": self.sentiment.examples,
            },
            "entities": {
                "total_unique": self.entities.total_unique,
                "by_type": self.entities.by_type,
            },
            "trend": {
                "direction": self.trend.direction,
                "total_posts": self.trend.total_posts,
                "volume": self.trend.volume,
                "sentiment": self.trend.sentiment,
                "platform_breakdown": self.trend.platform_breakdown,
            },
            "top_posts": self.top_posts,
        }
