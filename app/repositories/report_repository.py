"""All database queries for Report model live here."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reports.models import Report


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, report_id: uuid.UUID) -> Report | None:
        # TODO: Phase 7
        raise NotImplementedError

    async def list_by_project(self, project_id: uuid.UUID) -> list[Report]:
        # TODO: Phase 7
        raise NotImplementedError

    async def create(self, report: Report) -> Report:
        # TODO: Phase 7
        raise NotImplementedError

    async def update(self, report: Report) -> Report:
        # TODO: Phase 7
        raise NotImplementedError
