import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _run_scheduled_reports(period: str) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.repositories.keyword_repository import KeywordRepository
    from app.services.reports.schemas import GenerateReportRequest
    from app.services.reports.service import ReportService

    generated = []
    async with AsyncSessionLocal() as db:
        async with db.begin():
            kw_repo = KeywordRepository(db)
            keywords = await kw_repo.list_all_active()
            svc = ReportService(db)
            for kw in keywords:
                req = GenerateReportRequest(
                    keyword_id=kw.id,
                    project_id=kw.project_id,
                    format="json",
                    title=f"Auto Report — {kw.keyword} ({period})",
                )
                report = await svc.create_pending(req)
                generated.append(str(report.id))

    # Dispatch generate task per report
    from app.workers.report_worker import generate_report_task

    for report_id in generated:
        generate_report_task.delay(report_id)

    return {"scheduled": len(generated), "period": period}


@celery_app.task(
    name="workers.generate_scheduled_reports",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def generate_scheduled_reports_task(self, period: str = "day") -> dict:
    try:
        return asyncio.run(_run_scheduled_reports(period))
    except Exception as exc:
        raise self.retry(exc=exc)


async def _retry_missing_embeddings() -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.workers.ai_worker import analyze_post_task
    from app.services.processing.cleaner import default_cleaner
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        # Fix cleaned_content yang NULL atau kosong dulu
        rows_fix = await db.execute(text(
            "SELECT id, content FROM posts "
            "WHERE platform = 'youtube' "
            "AND embedding IS NULL "
            "AND (cleaned_content IS NULL OR TRIM(cleaned_content) = '') "
            "AND content IS NOT NULL AND TRIM(content) != '' "
            "LIMIT 200"
        ))
        to_fix = rows_fix.fetchall()

        for post_id, content in to_fix:
            try:
                cleaned = default_cleaner.clean(content)
            except Exception:
                cleaned = content
            if cleaned and cleaned.strip():
                await db.execute(text(
                    "UPDATE posts SET cleaned_content = :c, is_processed = TRUE WHERE id = :id"
                ), {"c": cleaned, "id": str(post_id)})

        if to_fix:
            await db.commit()
            logger.info(f"retry_missing_embeddings: fixed cleaned_content for {len(to_fix)} posts")

        # Dispatch embedding untuk semua posts tanpa embedding yang punya cleaned_content
        rows_emb = await db.execute(text(
            "SELECT id FROM posts "
            "WHERE platform = 'youtube' "
            "AND embedding IS NULL "
            "AND cleaned_content IS NOT NULL AND TRIM(cleaned_content) != '' "
            "ORDER BY collected_at DESC "
            "LIMIT 500"
        ))
        post_ids = [str(r[0]) for r in rows_emb.fetchall()]

    dispatched = 0
    for pid in post_ids:
        try:
            analyze_post_task.delay(pid, False, False, True)
            dispatched += 1
        except Exception as e:
            logger.warning(f"retry_missing_embeddings: dispatch failed {pid[:8]}: {e}")

    logger.info(f"retry_missing_embeddings: dispatched {dispatched} embedding tasks")
    return {"fixed_cleaned": len(to_fix), "dispatched_embeddings": dispatched}


@celery_app.task(
    name="workers.retry_missing_embeddings",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def retry_missing_embeddings_task(self) -> dict:
    """Periodic task: auto-fix posts yang belum punya embedding."""
    try:
        return asyncio.run(_retry_missing_embeddings())
    except Exception as exc:
        logger.error(f"retry_missing_embeddings_task error: {exc}")
        raise self.retry(exc=exc)
