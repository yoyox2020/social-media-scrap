import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class ThreadsSearchQueue(Base, UUIDMixin):
    """
    Antrian pencarian Threads yang tertunda -- diisi saat worker penuh
    (slot job paralel habis) ATAU semua token EnsembleData exhausted.
    Diproses ulang otomatis oleh task `threads-queue-drain` (tiap 10
    menit) sampai berhasil atau `attempts` melewati batas ->
    `failed_permanent`. Lihat docs/threads-redesign-schema.md.
    """
    __tablename__ = "threads_search_queue"

    keyword_text: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="manual | trend_recommendation | topic_search",
    )
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True,
        comment="pending | done | failed_permanent",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
