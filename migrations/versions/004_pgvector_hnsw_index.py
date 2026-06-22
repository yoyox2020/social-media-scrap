"""add HNSW index on posts.embedding and comments.embedding for fast ANN search

Revision ID: 004
Revises: 003
Create Date: 2026-06-22

HNSW (Hierarchical Navigable Small World) dipilih karena:
- Query lebih cepat dari IVFFLAT (tidak perlu `SET ivfflat.probes`)
- Tidak butuh training/vacuum sebelum dipakai (IVFFLAT butuh data cukup dulu)
- Mendukung insert incremental tanpa rebuild index

Operator class:
- vector_cosine_ops → cocok untuk BGE-M3 normalized embeddings (cosine = dot product)

Ref: https://github.com/pgvector/pgvector#indexing
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HNSW index untuk posts.embedding — dipakai semantic search di SearchAgent
    # m=16, ef_construction=64 adalah nilai default yang seimbang antara
    # build speed vs query recall (~98% recall pada dataset umum)
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_posts_embedding_hnsw
        ON posts
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # HNSW index untuk comments.embedding
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_comments_embedding_hnsw
        ON comments
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ef_search di-set per query di PostRepository.search_by_embedding
    # menggunakan SET LOCAL hnsw.ef_search = N sebelum query dieksekusi


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_comments_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_posts_embedding_hnsw")
