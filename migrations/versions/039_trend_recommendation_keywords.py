"""Keyword kustom per topik (2026-07-24, permintaan user "1 topik bisa
create beberapa keyword") -- tabel PENDAMPING trend_recommendations
(SAMA pola dgn migrasi 034 trend_recommendation_platform_usage), TIDAK
mengubah tabel trend_recommendations yg frozen. Kalau topik py >=1
keyword kustom di sini, agent_search.build_keywords() pakai keyword2
ini alih-alih 3-varian auto ("<topic>"/"<topic> terbaru"/"<topic> trending").

Revision ID: 039
Revises: 038
Create Date: 2026-07-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trend_recommendation_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trend_recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trend_recommendations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword_text", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_trend_reco_keywords_reco_id", "trend_recommendation_keywords", ["trend_recommendation_id"])
    op.create_unique_constraint(
        "uq_trend_reco_keyword", "trend_recommendation_keywords", ["trend_recommendation_id", "keyword_text"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_trend_reco_keyword", "trend_recommendation_keywords", type_="unique")
    op.drop_index("ix_trend_reco_keywords_reco_id", table_name="trend_recommendation_keywords")
    op.drop_table("trend_recommendation_keywords")
