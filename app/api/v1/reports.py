import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_db
from app.repositories.report_repository import ReportRepository
from app.services.reports.schemas import GenerateReportRequest, ReportJobResponse
from app.services.reports.service import ReportService
from app.workers.report_worker import generate_report_task

router = APIRouter(prefix="/reports", tags=["reports"])

_MIME = {
    "json": "application/json",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.post("/generate", status_code=202)
async def generate_report(
    req: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
) -> ReportJobResponse:
    """
    Trigger async report generation via Celery.
    Format: json | pdf | docx
    """
    if req.format not in ("json", "pdf", "docx"):
        raise HTTPException(status_code=422, detail="format harus json, pdf, atau docx")

    svc = ReportService(db)
    report = await svc.create_pending(req)
    await db.commit()

    job = generate_report_task.delay(str(report.id))

    return ReportJobResponse(
        report_id=report.id,
        job_id=job.id,
        status="pending",
        format=req.format,
    )


@router.post("/generate-sync", status_code=200)
async def generate_report_sync(
    req: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate laporan secara sinkron (tanpa Celery) — untuk testing."""
    if req.format not in ("json", "pdf", "docx"):
        raise HTTPException(status_code=422, detail="format harus json, pdf, atau docx")

    svc = ReportService(db)
    report = await svc.create_pending(req)
    await db.flush()

    report = await svc.generate(report.id)
    await db.commit()

    return {
        "report_id": str(report.id),
        "status": report.status,
        "format": report.format,
        "file_path": report.file_path,
        "summary": report.summary,
        "data": report.data,
    }


@router.get("/", status_code=200)
async def list_reports(
    project_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Daftar laporan milik satu project."""
    repo = ReportRepository(db)
    reports = await repo.list_by_project(project_id, limit=limit, offset=offset)
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "format": r.format,
            "status": r.status,
            "keyword_id": str(r.keyword_id) if r.keyword_id else None,
            "file_path": r.file_path,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]


@router.get("/{report_id}", status_code=200)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Detail satu laporan termasuk preview data JSON."""
    repo = ReportRepository(db)
    report = await repo.get_by_id(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report tidak ditemukan")

    return {
        "id": str(report.id),
        "title": report.title,
        "summary": report.summary,
        "format": report.format,
        "status": report.status,
        "keyword_id": str(report.keyword_id) if report.keyword_id else None,
        "file_path": report.file_path,
        "data": report.data,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


@router.get("/{report_id}/download")
async def download_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download file laporan (PDF / DOCX / JSON)."""
    repo = ReportRepository(db)
    report = await repo.get_by_id(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report tidak ditemukan")
    if report.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Report belum selesai. Status: {report.status}",
        )
    if not report.file_path or not Path(report.file_path).exists():
        raise HTTPException(status_code=404, detail="File laporan tidak ditemukan di server")

    fmt = report.format or "json"
    filename = f"report_{report_id}.{fmt}"
    return FileResponse(
        path=report.file_path,
        media_type=_MIME.get(fmt, "application/octet-stream"),
        filename=filename,
    )


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hapus record report dari DB (file di disk tidak dihapus)."""
    repo = ReportRepository(db)
    deleted = await repo.delete(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report tidak ditemukan")
    await db.commit()
