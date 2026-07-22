"""Log aktivitas pipeline multi-agent (2026-07-22, permintaan user) --
1 baris per event tiap tahap pipeline (agent_topic/agent_search/
agent_youtube01/agent_youtube02/agent-struktur-data), dikelompokkan
per run_id. Terpisah dari `scrape_runs` (itu ringkasan 1 baris per
run, ini detail langkah-per-langkah DI DALAM 1 run).

Revision ID: 035
Revises: 034
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_activity_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("details", postgresql.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_activity_log_run_id", "agent_activity_log", ["run_id"])
    op.create_index("ix_agent_activity_log_agent_name", "agent_activity_log", ["agent_name"])


def downgrade() -> None:
    op.drop_index("ix_agent_activity_log_agent_name", table_name="agent_activity_log")
    op.drop_index("ix_agent_activity_log_run_id", table_name="agent_activity_log")
    op.drop_table("agent_activity_log")
