"""Fase 3 redesain Threads (2026-07-20/21, docs/threads-redesign-schema.md
§3.1) -- tabel PENDAMPING baru, additive only, TIDAK menyentuh tabel
`trend_recommendations` (FROZEN, lihat feedback_trend_recommendations_frozen)
maupun kode platform lain sama sekali.

Latar: kolom `trend_recommendations.status` DIBAGI BERSAMA semua platform
(dikonfirmasi juga di docstring lama
app/services/trend_recommendations/service.py::mark_failed_permanent_if_exhausted:
"hitungan gagal ini LINTAS PLATFORM ... bukan per-platform"). Threads
sekarang TIDAK LAGI baca/tulis `status` sama sekali -- pakai tabel ini
utk tracking "topik mana yang SUDAH pernah dicoba Threads", independen
dari platform lain.

Revision ID: 026
Revises: 025
Create Date: 2026-07-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trend_recommendation_platform_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trend_recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trend_recommendations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("trend_recommendation_id", "platform", name="uq_trend_reco_platform_usage"),
    )
    op.create_index(
        "ix_trend_reco_platform_usage_platform",
        "trend_recommendation_platform_usage", ["platform"],
    )


def downgrade() -> None:
    op.drop_index("ix_trend_reco_platform_usage_platform", table_name="trend_recommendation_platform_usage")
    op.drop_table("trend_recommendation_platform_usage")
