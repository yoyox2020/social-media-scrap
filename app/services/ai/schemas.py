import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass
class SentimentResult:
    label: str          # positive | negative | neutral
    score: float        # confidence 0.0–1.0
    model_version: str = ""


@dataclass
class EntityResult:
    text: str
    entity_type: str
    start_char: int
    end_char: int
    score: float


@dataclass
class AIAnalysisResult:
    post_id: uuid.UUID
    sentiment: SentimentResult | None = None
    entities: list[EntityResult] = field(default_factory=list)
    embedding_updated: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "post_id": str(self.post_id),
            "sentiment": {
                "label": self.sentiment.label,
                "score": self.sentiment.score,
                "model_version": self.sentiment.model_version,
            } if self.sentiment else None,
            "entities_count": len(self.entities),
            "embedding_updated": self.embedding_updated,
            "errors": self.errors,
        }


@dataclass
class KeywordAnalysisStats:
    keyword_id: uuid.UUID
    total_posts: int = 0
    analyzed: int = 0
    sentiment_positive: int = 0
    sentiment_negative: int = 0
    sentiment_neutral: int = 0
    entities_extracted: int = 0
    embeddings_generated: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "keyword_id": str(self.keyword_id),
            "total_posts": self.total_posts,
            "analyzed": self.analyzed,
            "sentiment_distribution": {
                "positive": self.sentiment_positive,
                "negative": self.sentiment_negative,
                "neutral": self.sentiment_neutral,
            },
            "entities_extracted": self.entities_extracted,
            "embeddings_generated": self.embeddings_generated,
            "errors": self.errors,
        }


class AnalyzeRequest(BaseModel):
    keyword_id: uuid.UUID
    force_reanalyze: bool = False
    run_sentiment: bool = True
    run_ner: bool = True
    run_embedding: bool = True


class AnalyzePostRequest(BaseModel):
    post_id: uuid.UUID
    run_sentiment: bool = True
    run_ner: bool = True
    run_embedding: bool = True


class AnalyzeJobResponse(BaseModel):
    keyword_id: uuid.UUID
    job_id: str
    status: str = "queued"
