import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reports.models import Report


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, report_id: uuid.UUID) -> Report | None:
        result = await self.db.execute(select(Report).where(Report.id == report_id))
        return result.scalar_one_or_none()

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Report]:
        result = await self.db.execute(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_by_keyword(self, keyword_id: uuid.UUID) -> list[Report]:
        result = await self.db.execute(
            select(Report)
            .where(Report.keyword_id == keyword_id)
            .order_by(Report.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, report: Report) -> Report:
        self.db.add(report)
        await self.db.flush()
        await self.db.refresh(report)
        return report

    async def update_status(self, report_id: uuid.UUID, status: str) -> None:
        await self.db.execute(
            update(Report).where(Report.id == report_id).values(status=status)
        )
        await self.db.flush()

    async def update_after_generate(
        self,
        report_id: uuid.UUID,
        file_path: str,
        summary: str,
        data: dict,
        status: str = "done",
    ) -> None:
        await self.db.execute(
            update(Report)
            .where(Report.id == report_id)
            .values(
                file_path=file_path,
                summary=summary,
                data=data,
                status=status,
            )
        )
        await self.db.flush()

    async def delete(self, report_id: uuid.UUID) -> bool:
        report = await self.get_by_id(report_id)
        if not report:
            return False
        await self.db.delete(report)
        await self.db.flush()
        return True
