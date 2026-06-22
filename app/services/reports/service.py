"""ReportService — orchestrator untuk generate laporan."""

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reports.models import Report
from app.repositories.report_repository import ReportRepository
from app.services.reports.data_collector import ReportDataCollector
from app.services.reports.docx_generator import DOCXReportGenerator
from app.services.reports.json_generator import JSONReportGenerator
from app.services.reports.pdf_generator import PDFReportGenerator
from app.services.reports.schemas import GenerateReportRequest, ReportData
from app.shared.config import settings

GENERATORS = {
    "json": JSONReportGenerator,
    "pdf": PDFReportGenerator,
    "docx": DOCXReportGenerator,
}
VALID_FORMATS = frozenset(GENERATORS.keys())


class ReportService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ReportRepository(db)
        self.collector = ReportDataCollector(db)

    async def create_pending(self, req: GenerateReportRequest) -> Report:
        """Buat record report dengan status pending, kembalikan object-nya."""
        report = Report(
            project_id=req.project_id,
            keyword_id=req.keyword_id,
            title=req.title or f"Laporan {req.format.upper()}",
            format=req.format,
            status="pending",
        )
        return await self.repo.create(report)

    async def generate(self, report_id: uuid.UUID) -> Report:
        """
        Tahap utama yang dipanggil oleh Celery worker:
        1. Load report record dari DB
        2. Kumpulkan data
        3. Generate file
        4. Simpan path + status=done
        """
        report = await self.repo.get_by_id(report_id)
        if report is None:
            raise ValueError(f"Report {report_id} tidak ditemukan")

        # Tandai sedang berjalan
        await self.repo.update_status(report_id, "generating")

        try:
            data: ReportData = await self.collector.collect(
                keyword_id=report.keyword_id,
                report_id=report_id,
                title=report.title,
                period="day",
                posts_sample_size=5,
            )

            fmt = report.format
            if fmt not in VALID_FORMATS:
                fmt = "json"

            output_dir = settings.report_output_dir
            generator = GENERATORS[fmt]()
            file_path = generator.generate(data, output_dir)

            # Simpan ringkasan JSON ke kolom `data` (untuk preview di frontend)
            summary_dict = {
                "total_posts": data.total_posts,
                "sentiment": {
                    "dominant": data.sentiment.dominant,
                    "distribution": data.sentiment.distribution,
                },
                "entities_unique": data.entities.total_unique,
                "trend_direction": data.trend.direction,
            }

            await self.repo.update_after_generate(
                report_id=report_id,
                file_path=file_path,
                summary=data.keyword_text,
                data=summary_dict,
                status="done",
            )

        except Exception as exc:
            await self.repo.update_status(report_id, "failed")
            raise

        return await self.repo.get_by_id(report_id)

    async def get_file_path(self, report_id: uuid.UUID) -> str | None:
        report = await self.repo.get_by_id(report_id)
        if report and report.file_path and Path(report.file_path).exists():
            return report.file_path
        return None
