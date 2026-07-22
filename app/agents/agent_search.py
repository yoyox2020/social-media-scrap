"""agent_search (2026-07-22) -- cari topik trending + keyword utama +
keyword turunan + prioritas, kirim ke agent_youtube (parent/coordinator).

MVP (versi sederhana): TIDAK ada deteksi trending eksternal yg canggih
di versi ini -- keyword utama = topic apa adanya, keyword turunan =
variasi simpel ("<topic> terbaru", "<topic> trending"). Prioritas
1=utama, 2..n=turunan (urutan pemakaian saat fetch, bukan skor
statistik)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity

AGENT_NAME = "agent_search"


async def build_keywords(db: AsyncSession, run_id: uuid.UUID, topic: str, platform: str = "youtube") -> dict:
    keywords = [
        {"keyword": topic, "priority": 1, "kind": "utama"},
        {"keyword": f"{topic} terbaru", "priority": 2, "kind": "turunan"},
        {"keyword": f"{topic} trending", "priority": 3, "kind": "turunan"},
    ]

    await log_activity(
        db, run_id, AGENT_NAME, "build_keywords",
        f"{len(keywords)} keyword disusun utk topik '{topic}'",
        details={"keywords": keywords},
    )

    return {"platform": platform, "topic": topic, "keywords": keywords}
