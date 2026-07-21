"""tambah kolom fallback_used di youtube_discovery_runs -- key/model
CADANGAN ("agent 2") dipakai validate_candidate() saat key/model UTAMA kena
rate-limit (429), permintaan user 2026-07-18 supaya kandidat yg tadinya
di-skip konservatif krn limit habis bisa tetap divalidasi+tersimpan lewat
key kedua. Kolom ini hitung berapa kandidat per-run yg lolos berkat fallback.

Revision ID: 022
Revises: 021
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "youtube_discovery_runs",
        sa.Column("fallback_used", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("youtube_discovery_runs", "fallback_used")
