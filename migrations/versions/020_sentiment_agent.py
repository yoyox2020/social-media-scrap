"""tambah kolom LLM sentiment second-opinion ke lexicon_analyses -- Sentiment
Agent (2026-07-18). Lexicon (rule-based, khusus Bahasa Indonesia) terbukti
lewat data nyata sering salah label "netral" utk komentar BUKAN Bahasa
Indonesia (Inggris/Spanyol/Arab dst -- otomatis skor 0 krn tidak ada kata yg
cocok kamus Indonesia) ATAU komentar Indonesia berslang yg tidak ada di
kamus. Sentiment Agent kasih opini KEDUA dari LLM (model gratis OpenRouter)
utk kasus2 itu -- disimpan di kolom TERPISAH (bukan menimpa label/score
lexicon asli), supaya kedua hasil bisa dibandingkan/diaudit.

Revision ID: 020
Revises: 019
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lexicon_analyses", sa.Column("detected_language", sa.String(20), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("llm_label", sa.String(20), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("llm_model", sa.String(255), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("llm_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lexicon_analyses", sa.Column("sentiment_agreement", sa.Boolean(), nullable=True))
    op.create_index("ix_lexicon_analyses_llm_checked_at", "lexicon_analyses", ["llm_checked_at"])


def downgrade() -> None:
    op.drop_index("ix_lexicon_analyses_llm_checked_at", table_name="lexicon_analyses")
    op.drop_column("lexicon_analyses", "sentiment_agreement")
    op.drop_column("lexicon_analyses", "llm_checked_at")
    op.drop_column("lexicon_analyses", "llm_model")
    op.drop_column("lexicon_analyses", "llm_label")
    op.drop_column("lexicon_analyses", "detected_language")
