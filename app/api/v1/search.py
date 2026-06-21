from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.agents.schemas import SearchRequest
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/semantic", response_model=dict)
async def semantic_search(
    body: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Semantic search menggunakan pgvector (cosine similarity dari BGE-M3 embeddings).
    Memerlukan Phase 4 sudah berjalan (posts.embedding harus terisi).
    """
    from app.repositories.post_repository import PostRepository
    from app.services.ai.embedding_generator import EmbeddingGenerator

    embedding = EmbeddingGenerator.get_instance().generate(body.query)
    repo = PostRepository(db)
    posts = await repo.search_by_embedding(
        embedding=embedding,
        keyword_id=body.keyword_id,
        limit=body.limit,
    )
    return build_success_response({
        "query": body.query,
        "mode": "semantic",
        "results": [
            {
                "post_id": str(p.id),
                "excerpt": (p.cleaned_content or p.content or "")[:300],
                "author": p.author,
                "platform": p.platform,
                "published_at": p.published_at.isoformat() if p.published_at else None,
            }
            for p in posts
        ],
        "total": len(posts),
    })


@router.post("/fulltext", response_model=dict)
async def fulltext_search(
    body: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Full-text search menggunakan PostgreSQL tsvector.
    Cepat, tidak perlu embedding. Mendukung Bahasa Indonesia dengan 'simple' dictionary.
    """
    from sqlalchemy import func, select, text
    from app.domain.posts.models import Post

    query_words = " | ".join(w for w in body.query.split() if len(w) > 2)
    if not query_words:
        return build_success_response({"query": body.query, "results": [], "total": 0})

    stmt = (
        select(Post)
        .where(
            Post.keyword_id == body.keyword_id,
            Post.cleaned_content.is_not(None),
            func.to_tsvector("simple", Post.cleaned_content).op("@@")(
                func.to_tsquery("simple", query_words)
            ),
        )
        .order_by(Post.published_at.desc())
        .limit(body.limit)
    )
    result = await db.execute(stmt)
    posts = result.scalars().all()

    return build_success_response({
        "query": body.query,
        "mode": "fulltext",
        "results": [
            {
                "post_id": str(p.id),
                "excerpt": (p.cleaned_content or p.content or "")[:300],
                "author": p.author,
                "platform": p.platform,
                "published_at": p.published_at.isoformat() if p.published_at else None,
            }
            for p in posts
        ],
        "total": len(posts),
    })
