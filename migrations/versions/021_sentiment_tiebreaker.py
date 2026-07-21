"""tambah tie-breaker LLM kedua + tabel usulan kata kamus lexicon
(Sentiment Agent, 2026-07-18). Saat lexicon vs LLM pertama TIDAK sepakat,
LLM KEDUA (model/provider berbeda) dipanggil sbg suara ketiga -- hasil
mayoritas 2-dari-3 disimpan sbg `final_label`. Kalau mayoritas MENGALAHKAN
lexicon, kata-kata dari komentar itu yg BELUM ada di kamus lexicon dicatat
sbg usulan (BUKAN auto-ditambahkan -- tinjau manual dulu sebelum masuk
app/ai/lexicon/data/*.txt, krn lexicon dipakai lintas platform FB/IG/TikTok/
Twitter/YouTube, bukan cuma YouTube).

Revision ID: 021
Revises: 020
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lexicon_analyses", sa.Column("llm2_label", sa.String(20), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("llm2_model", sa.String(255), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("final_label", sa.String(20), nullable=True))

    op.create_table(
        "lexicon_word_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("word", sa.String(100), nullable=False),
        sa.Column("suggested_polarity", sa.String(20), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("example_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("word", "suggested_polarity", name="uq_lexicon_word_suggestions_word_polarity"),
    )
    op.create_index("ix_lexicon_word_suggestions_evidence_count", "lexicon_word_suggestions", ["evidence_count"])


def downgrade() -> None:
    op.drop_index("ix_lexicon_word_suggestions_evidence_count", table_name="lexicon_word_suggestions")
    op.drop_table("lexicon_word_suggestions")
    op.drop_column("lexicon_analyses", "final_label")
    op.drop_column("lexicon_analyses", "llm2_model")
    op.drop_column("lexicon_analyses", "llm2_label")
