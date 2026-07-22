"""Katalog API pihak ketiga + link ke agent (2026-07-22, permintaan
user) -- BEDA dari agent_key_pool (itu key MILIK 1 agent, rotasi
otomatis). Ini katalog BEBAS ditambah (mis. "Apify Akun 1", "OpenRouter
Bersama") yang bisa DIHUBUNGKAN ke BANYAK agent sekaligus (many-to-many)
-- 1 API pihak ketiga bisa dipakai banyak agent, 1 agent bisa pakai
banyak API pihak ketiga.

Revision ID: 033
Revises: 032
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "third_party_apis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("api_key", sa.Text, nullable=True),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("account_email", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_third_party_apis_provider", "third_party_apis", ["provider"])

    op.create_table(
        "third_party_api_agent_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("third_party_api_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("third_party_apis.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("third_party_api_id", "agent_name", name="uq_third_party_api_agent"),
    )
    op.create_index("ix_third_party_api_agent_links_agent_name", "third_party_api_agent_links", ["agent_name"])


def downgrade() -> None:
    op.drop_index("ix_third_party_api_agent_links_agent_name", table_name="third_party_api_agent_links")
    op.drop_table("third_party_api_agent_links")
    op.drop_index("ix_third_party_apis_provider", table_name="third_party_apis")
    op.drop_table("third_party_apis")
