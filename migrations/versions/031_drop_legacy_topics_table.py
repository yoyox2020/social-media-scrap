"""Hapus tabel `topics` lama (2026-07-22, permintaan user) -- BEDA dari
`search_topics` (53 topik nyata, TIDAK disentuh). Tabel `topics` adalah
sisa desain paling awal proyek (keywords disimpan JSONB tidak
ternormalisasi, tied ke project_id), TIDAK PERNAH dipakai (0 baris,
tidak ada tabel lain yg reference ke sini via FK) -- sudah digantikan
total oleh search_topics/search_topic_keywords/keywords.

Revision ID: 031
Revises: 030
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("topics")


def downgrade() -> None:
    op.create_table(
        "topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("keywords", postgresql.JSON, nullable=True),
        sa.Column("post_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_topics_project_id", "topics", ["project_id"])
