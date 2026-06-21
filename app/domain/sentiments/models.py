import uuid

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class Sentiment(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sentiments"

    post_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    post: Mapped["Post"] = relationship("Post", back_populates="sentiments")  # noqa: F821
    comment: Mapped["Comment"] = relationship("Comment", back_populates="sentiments")  # noqa: F821
