"""tambah kolom post_published_at di topic_notifications -- snapshot tanggal
upload ASLI konten yang dinotifikasi (2026-07-20). Ambang batas viral di
fitur ini murni angka tetap (views/likes), TIDAK ada komponen waktu sama
sekali -- post lama yang baru ke-notif (msh dlm lookback_days & belum
pernah dinotif) tetap valid. Kolom ini supaya frontend bisa tampilkan
"diupload N hari lalu" tanpa perlu join ke tabel posts (post_id bisa
SET NULL kalau post dihapus, pola snapshot sama dgn title/author/url).

Revision ID: 024
Revises: 023
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "topic_notifications",
        sa.Column("post_published_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topic_notifications", "post_published_at")
