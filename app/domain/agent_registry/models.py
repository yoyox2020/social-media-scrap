from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class AgentRegistryEntry(Base, UUIDMixin):
    """
    Katalog "Kelola Agent" -- 1 baris = 1 key/model yang dipakai 1 agent
    (1 agent bisa punya beberapa baris, mis. Discovery Agent 1 py 3:
    YouTube key + OpenRouter utama + OpenRouter cadangan).

    `linked_credential_id` NOT NULL -> key SEBENARNYA dibaca/ditulis lewat
    /api/v1/credentials/{linked_credential_id} yang SUDAH ADA (TIDAK
    disalin ke sini, cuma referensi utk pengelompokan tampilan).
    `linked_credential_id` NULL -> agent BARU dari form, key/model
    disimpan LANGSUNG di custom_api_key/custom_model.
    """
    __tablename__ = "agent_registry"

    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_label: Mapped[str] = mapped_column(String(100), nullable=False)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linked_credential_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    custom_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
