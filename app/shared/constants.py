from enum import Enum


class Platform(str, Enum):
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    NEWS = "news"
    FORUM = "forum"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ReportFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    JSON = "json"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"


EMBEDDING_DIMENSION = 1024
MAX_KEYWORD_LENGTH = 255
MAX_CONTENT_LENGTH = 10000
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
