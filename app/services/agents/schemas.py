"""Schemas untuk Agent Service."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel


# ── Request ───────────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    keyword_id: uuid.UUID
    platform: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    use_llm_planner: bool = False  # True = pakai Qwen3 untuk planning; False = rule-based


class TopicDetectRequest(BaseModel):
    keyword_id: uuid.UUID
    num_topics: int = 5
    force: bool = False


class TrendRequest(BaseModel):
    keyword_id: uuid.UUID
    period: str = "day"   # day | week | month
    platform: str | None = None


class SearchRequest(BaseModel):
    query: str
    keyword_id: uuid.UUID
    mode: str = "semantic"  # semantic | fulltext | hybrid
    limit: int = 10


# ── Context shared ke semua agents ────────────────────────────────────────────

@dataclass
class AgentContext:
    question: str
    keyword_id: uuid.UUID
    keyword_text: str = ""
    platform: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


# ── Per-agent result ──────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    agent_name: str
    data: dict = field(default_factory=dict)
    summary: str = ""
    sources: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "summary": self.summary,
            "data": self.data,
            "sources": self.sources,
            "error": self.error,
        }


# ── Final aggregated response ─────────────────────────────────────────────────

@dataclass
class AgentResponse:
    question: str
    keyword_id: uuid.UUID
    answer: str
    agent_plan: list[str]
    details: dict[str, AgentResult] = field(default_factory=dict)
    processing_time_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "keyword_id": str(self.keyword_id),
            "answer": self.answer,
            "agent_plan": self.agent_plan,
            "details": {k: v.to_dict() for k, v in self.details.items()},
            "processing_time_ms": self.processing_time_ms,
            "errors": self.errors,
        }


class AskJobResponse(BaseModel):
    keyword_id: uuid.UUID
    job_id: str
    status: str = "queued"
