"""BaseAgent — abstract contract yang diimplementasikan oleh setiap specialized agent."""
from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agents.schemas import AgentContext, AgentResult


class BaseAgent(ABC):
    name: str = "base"
    description: str = ""

    def __init__(self, db: AsyncSession):
        self.db = db

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Jalankan agent dan return hasilnya."""

    def _ok(self, data: dict, summary: str, sources: list[dict] | None = None) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            data=data,
            summary=summary,
            sources=sources or [],
        )

    def _err(self, message: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            error=message,
            summary=f"[{self.name}] gagal: {message}",
        )
