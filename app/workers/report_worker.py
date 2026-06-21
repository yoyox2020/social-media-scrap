from app.workers.celery_app import celery_app


@celery_app.task(name="workers.generate_report")
def generate_report(report_id: str) -> dict:
    # TODO: Phase 7 - generate PDF/DOCX/JSON report → upload → update DB
    raise NotImplementedError
