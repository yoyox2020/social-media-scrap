import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.integrations.ensemble_data.endpoints import SUPPORTED_COLLECTION_PLATFORMS


class CollectRequest(BaseModel):
    keyword_id: uuid.UUID
    platforms: list[str] = SUPPORTED_COLLECTION_PLATFORMS

    def validate_platforms(self) -> None:
        invalid = [p for p in self.platforms if p not in SUPPORTED_COLLECTION_PLATFORMS]
        if invalid:
            raise ValueError(f"Platform tidak didukung: {invalid}. Pilih dari: {SUPPORTED_COLLECTION_PLATFORMS}")


class CollectJobResponse(BaseModel):
    keyword_id: uuid.UUID
    keyword_text: str
    jobs: list[dict]


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: dict | None = None


@dataclass
class CollectionResult:
    platform: str
    keyword: str
    total_fetched: int = 0
    new_posts: int = 0
    skipped_duplicates: int = 0
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "keyword": self.keyword,
            "total_fetched": self.total_fetched,
            "new_posts": self.new_posts,
            "skipped_duplicates": self.skipped_duplicates,
            "pages_fetched": self.pages_fetched,
            "errors": self.errors,
        }
