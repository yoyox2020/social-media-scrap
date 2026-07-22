"""agent_topic (2026-07-22) -- terima request user, tentukan
objective+topik, kirim ke agent_search. TIDAK boleh ambil data
(sesuai spec), murni penentuan topik saja.

MVP: objective ditentukan simpel dari topic yg diberikan user (tidak
ada NLP intent-detection canggih di versi ini)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity

AGENT_NAME = "agent_topic"


async def determine_topic(db: AsyncSession, run_id: uuid.UUID, user_topic: str, platform: str = "youtube") -> dict:
    topic = user_topic.strip()
    objective = f"Cari & kumpulkan konten {platform} terbaru/trending terkait '{topic}'"

    await log_activity(
        db, run_id, AGENT_NAME, "determine_topic",
        f"Topik diterima: '{topic}' (platform={platform})",
        details={"topic": topic, "platform": platform, "objective": objective},
    )

    return {"topic": topic, "platform": platform, "objective": objective}
