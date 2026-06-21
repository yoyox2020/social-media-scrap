import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin
from app.shared.constants import EMBEDDING_DIMENSION


class Comment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "comments"

    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    post: Mapped["Post"] = relationship("Post", back_populates="comments")  # noqa: F821
    sentiments: Mapped[list["Sentiment"]] = relationship("Sentiment", back_populates="comment", lazy="noload")  # noqa: F821
    entities: Mapped[list["Entity"]] = relationship("Entity", back_populates="comment", lazy="noload")  # noqa: F821
