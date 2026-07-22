from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDMixin


class ThirdPartyApi(Base, UUIDMixin):
    """
    Katalog API pihak ketiga (2026-07-22) -- BEBAS ditambah (Apify,
    OpenRouter, EnsembleData, Firecrawl, dll), TERPISAH dari
    agent_key_pool (yg khusus 1 key milik 1 agent). Satu baris di sini
    bisa dihubungkan ke BANYAK agent lewat ThirdPartyApiAgentLink.
    """
    __tablename__ = "third_party_apis"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Log error/habis TERAKHIR (2026-07-22, permintaan user) -- SIMPLE,
    # tampil langsung di kartu list, BUKAN sistem status/reload
    # terpisah spt rotation_key_bank.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ThirdPartyApiAgentLink(Base, UUIDMixin):
    """Many-to-many: 1 API pihak ketiga <-> banyak agent (by agent_name,
    cocok konsep sama dgn agent_registry/agent_key_pool -- bukan FK id
    krn "agent" itu sendiri = kumpulan baris yg berbagi nama)."""
    __tablename__ = "third_party_api_agent_links"
    __table_args__ = (
        UniqueConstraint("third_party_api_id", "agent_name", name="uq_third_party_api_agent"),
    )

    third_party_api_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("third_party_apis.id", ondelete="CASCADE"), nullable=False,
    )
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
