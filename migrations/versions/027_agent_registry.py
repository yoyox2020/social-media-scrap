"""Tabel katalog "Kelola Agent" (2026-07-22, permintaan user) --
mengelompokkan SEMUA agent AI (YouTube Discovery 1/2, Metadata, Views
Refresh, Sentiment, Instagram Backfill, Threads, dll) dalam satu tabel,
plus mekanisme registrasi agent BARU lewat form dashboard.

Desain SENGAJA tidak menduplikasi penyimpanan API key yang sudah ada di
Redis (app/services/credentials/registry.py) -- baris utk agent yang
SUDAH py kode asli menaruh id credential yang sudah ada di
`linked_credential_id` (nilai key-nya tetap dibaca/ditulis lewat
endpoint /api/v1/credentials yang sudah teruji, BUKAN disalin ke sini).
Baris utk agent BARU (belum py kode scraping asli, cuma dicatat dulu)
`linked_credential_id` NULL, key/model disimpan LANGSUNG di
`custom_api_key`/`custom_model` kolom sendiri.

Revision ID: 027
Revises: 026
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("key_label", sa.String(100), nullable=False),
        sa.Column("linked_credential_id", sa.String(100), nullable=True),
        sa.Column("custom_api_key", sa.Text, nullable=True),
        sa.Column("custom_model", sa.String(255), nullable=True),
        sa.Column("is_custom", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_registry_agent_name", "agent_registry", ["agent_name"])


def downgrade() -> None:
    op.drop_index("ix_agent_registry_agent_name", table_name="agent_registry")
    op.drop_table("agent_registry")
