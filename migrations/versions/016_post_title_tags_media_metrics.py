"""tambah kolom title/tags/media/metrics ke posts -- persiapan skema
unified content utk agent discovery (lihat riwayat percakapan 2026-07-18).
Semua nullable, ditambahkan sebagai kolom baru (bukan restrukturisasi
metadata JSON yang sudah ada) supaya bisa di-index/di-query langsung.

Revision ID: 016
Revises: 015
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("posts", sa.Column("tags", postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column("posts", sa.Column("media", postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column("posts", sa.Column("metrics", postgresql.JSON(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "metrics")
    op.drop_column("posts", "media")
    op.drop_column("posts", "tags")
    op.drop_column("posts", "title")
