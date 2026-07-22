"""Info error/habis per-kartu di katalog API Pihak Ketiga (2026-07-22,
permintaan user) -- SIMPLE: cuma catat pesan+waktu error TERAKHIR
langsung di baris `third_party_apis` itu sendiri, ditampilkan di kartu
list-nya. TIDAK bikin sistem status/reload terpisah (user minta versi
sederhana, cukup info log per kotak).

Revision ID: 037
Revises: 036
Create Date: 2026-07-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("third_party_apis", sa.Column("last_error", sa.Text, nullable=True))
    op.add_column("third_party_apis", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("third_party_apis", "last_error_at")
    op.drop_column("third_party_apis", "last_error")
