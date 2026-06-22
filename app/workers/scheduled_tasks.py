import asyncio

from app.workers.celery_app import celery_app


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
