from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class YouTubeDiscoveryRun(Base, UUIDMixin):
    """
    Riwayat tiap run YouTube Discovery Agent (topic-guided + free discovery,
    lihat app/services/youtube_discovery/agent.py) -- status monitor DAN
    rincian per-kandidat (kolom `details`) utk dianalisis, bukan cuma
    angka ringkasan.
    """
    __tablename__ = "youtube_discovery_runs"

    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True, comment="running | success | failed")
    agent_label: Mapped[str] = mapped_column(String(20), nullable=False, server_default="agent1", index=True, comment="agent1 (topic+free, key sendiri) | agent2 (HANYA topic-guided, key TERPISAH, tiap 1 jam)")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    topics_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_validated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posts_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="jumlah kandidat yg lolos berkat key/model cadangan (agent 2)")
    model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
