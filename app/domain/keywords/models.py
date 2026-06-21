import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class Keyword(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "keywords"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    project: Mapped["Project"] = relationship("Project", back_populates="keywords")  # noqa: F821
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="keyword", lazy="noload")  # noqa: F821
