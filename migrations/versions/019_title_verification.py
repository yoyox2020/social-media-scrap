"""tambah kolom verifikasi judul vs id video di youtube_video_metadata --
verifikasi OBJEKTIF (bandingkan title tersimpan vs title asli YouTube per
video_id yang sama), BUKAN fact-check konten AI. Kalau beda, CUMA
ditandai/dicatat (title_mismatch + title_live), title tersimpan TIDAK
ditimpa otomatis -- permintaan user 2026-07-18.

Revision ID: 019
Revises: 018
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("youtube_video_metadata", sa.Column("title_mismatch", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("youtube_video_metadata", sa.Column("title_live", sa.Text(), nullable=True))
    op.add_column("youtube_video_metadata", sa.Column("title_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("youtube_video_metadata", "title_checked_at")
    op.drop_column("youtube_video_metadata", "title_live")
    op.drop_column("youtube_video_metadata", "title_mismatch")
