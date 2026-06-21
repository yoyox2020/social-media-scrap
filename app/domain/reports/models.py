import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDMixin


class Report(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reports"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="json")
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="reports")  # noqa: F821
