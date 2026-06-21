"""All database queries for Project model live here."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.models import Project


class ProjectRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        # TODO: Phase 2
        raise NotImplementedError

    async def list_by_user(self, user_id: uuid.UUID) -> list[Project]:
        # TODO: Phase 2
        raise NotImplementedError

    async def create(self, project: Project) -> Project:
        # TODO: Phase 2
        raise NotImplementedError

    async def update(self, project: Project) -> Project:
        # TODO: Phase 2
        raise NotImplementedError

    async def delete(self, project_id: uuid.UUID) -> None:
        # TODO: Phase 2
        raise NotImplementedError
