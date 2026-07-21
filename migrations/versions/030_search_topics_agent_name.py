"""Tambah kolom agent_name ke search_topics (2026-07-22, API v2) --
kaitkan topik ke salah satu agent baru (agent_youtube/facebook/
instagram/threads/news/tiktok, lihat tabel agent_registry) yang
bertugas memprosesnya. Additive only, nullable -- 53 topik lama yang
sudah ada TIDAK terpengaruh (agent_name=NULL = belum ditugaskan).

Reuse struktur search_topics/search_topic_keywords/keywords yang SUDAH
ADA (bukan tabel baru) -- topik-keyword many-to-many, hapus topik TIDAK
menghapus keyword (lihat search_topic_keywords, ON DELETE CASCADE cuma
di topic_id, bukan keyword_id).

Revision ID: 030
Revises: 029
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_topics", sa.Column("agent_name", sa.String(255), nullable=True))
    op.create_index("ix_search_topics_agent_name", "search_topics", ["agent_name"])


def downgrade() -> None:
    op.drop_index("ix_search_topics_agent_name", table_name="search_topics")
    op.drop_column("search_topics", "agent_name")
