"""
Celery tasks untuk AI inference pipeline (sentiment, NER, embedding).
Dijalankan di worker-ai container yang punya ML dependencies (torch, transformers, gliner).
"""
import asyncio
import uuid

from app.workers.celery_app import celery_app


@celery_app.task(
    name="workers.analyze_post",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def analyze_post_task(
    self,
    post_id: str,
    run_sentiment: bool = True,
    run_ner: bool = True,
    run_embedding: bool = True,
    force: bool = False,
) -> dict:
    """
    Jalankan inference AI untuk satu post.

    Args:
        post_id:       UUID post yang akan dianalisis
        run_sentiment: True = jalankan IndoBERT sentiment
        run_ner:       True = jalankan GLiNER NER
        run_embedding: True = generate BGE-M3 embedding
        force:         True = overwrite hasil yang sudah ada

    Returns:
        dict AIAnalysisResult
    """
    try:
        # Dispose pool sebelum asyncio.run() baru — asyncpg connections tidak boleh
        # dibawa dari event loop sebelumnya ke event loop baru dalam Celery prefork.
        from app.infrastructure.database.connection import engine
        engine.dispose()
        return asyncio.run(
            _analyze_post(post_id, run_sentiment, run_ner, run_embedding, force)
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.analyze_keyword",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def analyze_keyword_task(
    self,
    keyword_id: str,
    force_reanalyze: bool = False,
    run_sentiment: bool = True,
    run_ner: bool = True,
    run_embedding: bool = True,
) -> dict:
    """
    Jalankan inference AI untuk semua post satu keyword.

    Args:
        keyword_id:      UUID keyword
        force_reanalyze: True = proses ulang post yang sudah ada hasil AI-nya
        run_sentiment/ner/embedding: toggle per komponen
    """
    try:
        from app.infrastructure.database.connection import engine
        engine.dispose()
        return asyncio.run(
            _analyze_keyword(
                keyword_id, force_reanalyze, run_sentiment, run_ner, run_embedding
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)


async def _analyze_post(
    post_id: str,
    run_sentiment: bool,
    run_ner: bool,
    run_embedding: bool,
    force: bool,
) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.ai.service import AIService

    async with AsyncSessionLocal() as db:
        service = AIService(db)
        result = await service.analyze_post(
            post_id=uuid.UUID(post_id),
            run_sentiment=run_sentiment,
            run_ner=run_ner,
            run_embedding=run_embedding,
            force=force,
        )
        await db.commit()
        return result.to_dict()


async def _analyze_keyword(
    keyword_id: str,
    force_reanalyze: bool,
    run_sentiment: bool,
    run_ner: bool,
    run_embedding: bool,
) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.ai.schemas import AnalyzeRequest
    from app.services.ai.service import AIService

    async with AsyncSessionLocal() as db:
        service = AIService(db)
        request = AnalyzeRequest(
            keyword_id=uuid.UUID(keyword_id),
            force_reanalyze=force_reanalyze,
            run_sentiment=run_sentiment,
            run_ner=run_ner,
            run_embedding=run_embedding,
        )
        stats = await service.analyze_keyword(request)
        return stats.to_dict()
