"""Log aktivitas tiap tahap pipeline multi-agent (2026-07-22) --
dipanggil semua agent (agent_topic, agent_search, agent_youtube01/02,
agent-struktur-data) supaya riwayat lengkap 1 run bisa ditelusuri."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_activity_log.models import AgentActivityLog
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


async def log_activity(
    db: AsyncSession, run_id: uuid.UUID, agent_name: str, stage: str, message: str,
    level: str = "info", details: dict | None = None,
) -> None:
    entry = AgentActivityLog(
        run_id=run_id, agent_name=agent_name, stage=stage, level=level,
        message=message, details=details, created_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    await db.commit()
    logger.info("agent_activity", run_id=str(run_id), agent_name=agent_name, stage=stage, message=message, level=level)


async def get_run_log(db: AsyncSession, run_id: uuid.UUID) -> list[dict]:
    from sqlalchemy import select

    rows = (await db.scalars(
        select(AgentActivityLog).where(AgentActivityLog.run_id == run_id).order_by(AgentActivityLog.created_at)
    )).all()
    return [
        {
            "agent_name": r.agent_name, "stage": r.stage, "level": r.level,
            "message": r.message, "details": r.details,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
