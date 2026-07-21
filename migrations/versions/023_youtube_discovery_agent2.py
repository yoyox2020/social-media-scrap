"""tambah kolom agent_label di youtube_discovery_runs -- Agent 2 (2026-07-18):
agent DISKOVERI TERPISAH dari Agent 1 (bukan sekadar fallback), bawa YouTube
Data API key SENDIRI + OpenRouter key/model SENDIRI + jadwal SENDIRI
(default tiap 1 jam), HANYA mode topic-guided (cari video baru terkait
topic-search yg SUDAH ada di sistem, TIDAK ada free-discovery). Kolom ini
bedakan riwayat run Agent 1 vs Agent 2 di tabel yg sama (monitoring/status
dashboard bisa filter per-agent).

Revision ID: 023
Revises: 022
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "youtube_discovery_runs",
        sa.Column("agent_label", sa.String(20), nullable=False, server_default="agent1"),
    )
    op.create_index("ix_youtube_discovery_runs_agent_label", "youtube_discovery_runs", ["agent_label"])


def downgrade() -> None:
    op.drop_index("ix_youtube_discovery_runs_agent_label", table_name="youtube_discovery_runs")
    op.drop_column("youtube_discovery_runs", "agent_label")
