from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class AgentKeyPool(Base, UUIDMixin):
    """
    Pool rotasi API key per agent (2026-07-22) -- lihat docstring migrasi
    029. 1 agent_name bisa punya BANYAK baris (kandidat key), rotasi pilih
    yang `status='active'`, urut `priority` lalu paling lama tidak dipakai.
    """
    __tablename__ = "agent_key_pool"

    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active",
        comment="active | exhausted | disabled",
    )
    exhausted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
