"""Log aktivitas tiap tahap pipeline multi-agent (2026-07-22) --
dipanggil semua agent (agent_topic, agent_search, agent_youtube01/02,
agent-struktur-data) supaya riwayat lengkap 1 run bisa ditelusuri.

_LOG_ACTIVITY_LOCK (2026-07-24, ditemukan live saat test Twitter):
coordinator platform manapun yg jalankan child PARALEL via
`asyncio.gather()` (Facebook/TikTok/Threads/News/Twitter) manggil
`log_activity(db, ...)` per child SELESAI -- kalau 2+ child selesai
brengsamaan, `db.commit()` di sini bentrok krn semua child BERBAGI 1
`AsyncSession` yg sama (`IllegalStateChangeError`, PERSIS pola bug yg
sama dgn rotasi key third_party_apis, cuma titik pemanggilannya beda
-- lock lama di situ TIDAK melindungi log_activity(). Fix SAMA:
kunci di titik PALING BAWAH (fungsi ini sendiri) supaya SEMUA pemanggil
(lama+baru, platform apa pun) otomatis terlindung tanpa perlu ingat
lock manual di tiap coordinator."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_activity_log.models import AgentActivityLog
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

_LOG_ACTIVITY_LOCK = asyncio.Lock()


async def log_activity(
    db: AsyncSession, run_id: uuid.UUID, agent_name: str, stage: str, message: str,
    level: str = "info", details: dict | None = None,
) -> None:
    entry = AgentActivityLog(
        run_id=run_id, agent_name=agent_name, stage=stage, level=level,
        message=message, details=details, created_at=datetime.now(timezone.utc),
    )
    async with _LOG_ACTIVITY_LOCK:
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
