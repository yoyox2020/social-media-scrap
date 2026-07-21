"""Redesain metode pencarian Threads (2026-07-20) -- additive only, tidak
ada data lama yang dihapus/diubah:

1. comments.parent_comment_id (nullable, self-referential FK) -- supaya
   sub-komentar/balasan-ke-balasan bisa dikelompokkan lewat ID, bukan
   cuma metadata.reply_to (nama akun, rawan salah cocok). NULL = tetap
   berarti balasan top-level, SAMA seperti perilaku semua baris lama.
2. threads_search_queue -- antrian pencarian Threads yang tertunda
   (worker penuh ATAU semua token EnsembleData exhausted), diproses
   ulang otomatis oleh task `threads-queue-drain`. Lihat
   docs/threads-redesign-schema.md untuk desain lengkap.

Revision ID: 025
Revises: 024
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column("parent_comment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "comments_parent_comment_id_fkey",
        "comments", "comments",
        ["parent_comment_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_comments_parent_comment_id", "comments", ["parent_comment_id"],
    )

    op.create_table(
        "threads_search_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("keyword_text", sa.String(255), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_threads_queue_status", "threads_search_queue", ["status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_threads_queue_status", table_name="threads_search_queue")
    op.drop_table("threads_search_queue")
    op.drop_index("ix_comments_parent_comment_id", table_name="comments")
    op.drop_constraint("comments_parent_comment_id_fkey", "comments", type_="foreignkey")
    op.drop_column("comments", "parent_comment_id")
