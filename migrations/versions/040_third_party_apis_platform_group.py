"""Tag platform per akun API Pihak Ketiga (2026-07-24, permintaan user
"setiap platform memiliki 1 group... jadi setiap agent pun hanya bisa
mengambil dari group tersebut" + "ketika saya input key sudah jelas
untuk platform yang mana, jadi saya tidak memilih milih lagi untuk
agent yang mana") -- 1 kolom simple (bebas isi: youtube/tiktok/facebook/
instagram/dll), dipakai get_next_available_key() utk rotasi TERISOLASI
per-platform (fallback ke pool tanpa tag/NULL kalau grup platform itu
kosong/exhausted, supaya kapasitas lama yg SUDAH ada tidak hilang).

Revision ID: 040
Revises: 039
Create Date: 2026-07-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("third_party_apis", sa.Column("platform_group", sa.String(100), nullable=True))
    op.create_index("ix_third_party_apis_platform_group", "third_party_apis", ["platform_group"])


def downgrade() -> None:
    op.drop_index("ix_third_party_apis_platform_group", table_name="third_party_apis")
    op.drop_column("third_party_apis", "platform_group")
