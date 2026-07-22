from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class RotationKeyBank(Base, UUIDMixin):
    """Bank key BERSAMA lintas provider (OpenRouter, Grok/xAI, dll) utk
    rotasi OTOMATIS (2026-07-22) -- BEDA dari agent_key_pool (pool
    KANDIDAT milik 1 agent tertentu). Key di sini BEBAS (belum tentu
    milik agent manapun) sampai di-assign otomatis oleh sistem saat
    ada agent yg key-nya gagal (401/402/429/dst)."""
    __tablename__ = "rotation_key_bank"

    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    assigned_to_agent: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
