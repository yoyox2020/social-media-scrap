import asyncio
import uuid

from app.workers.celery_app import celery_app


async def _run_generate(report_id: str) -> dict:
    from app.infrastructure.database.connection import AsyncSessionLocal
    from app.services.reports.service import ReportService

    async with AsyncSessionLocal() as db:
        async with db.begin():
            svc = ReportService(db)
            report = await svc.generate(uuid.UUID(report_id))
            return {
                "report_id": str(report.id),
                "status": report.status,
                "file_path": report.file_path,
                "format": report.format,
            }


@celery_app.task(
    name="workers.generate_report",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def generate_report_task(self, report_id: str) -> dict:
    try:
        return asyncio.run(_run_generate(report_id))
    except Exception as exc:
        raise self.retry(exc=exc)
