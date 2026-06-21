import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel


class ProcessRequest(BaseModel):
    keyword_id: uuid.UUID
    force_reprocess: bool = False


class ProcessJobResponse(BaseModel):
    keyword_id: uuid.UUID
    job_id: str
    status: str = "queued"


@dataclass
class ProcessResult:
    keyword_id: uuid.UUID
    total_posts: int = 0
    cleaned: int = 0
    near_duplicates_found: int = 0
    skipped_already_processed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "keyword_id": str(self.keyword_id),
            "total_posts": self.total_posts,
            "cleaned": self.cleaned,
            "near_duplicates_found": self.near_duplicates_found,
            "skipped_already_processed": self.skipped_already_processed,
            "errors": self.errors,
        }
