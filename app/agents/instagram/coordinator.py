"""agent_instagram (coordinator/parent) -- BEDA dari coordinator
Facebook/TikTok: TIDAK membagi keyword ke banyak child paralel, krn
actor Instagram scrape SEMUA username terkait topik dlm SATU panggilan
batch (lihat crawler_client.py) -- child (agent_instagram01) di sini
CUMA penanda "yg bertanggung jawab" (konsisten dgn desain 1-agent
"mengawasi" platform ini), bukan unit distribusi kerja paralel spt
platform lain."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.instagram.crawler_client import fetch_posts_for_topic

AGENT_NAME = "agent_instagram"
RESPONSIBLE_CHILD = "agent_instagram01"


async def run_children(db: AsyncSession, run_id: uuid.UUID, topic: str) -> dict:
    result = await fetch_posts_for_topic(db, topic)
    await log_activity(
        db, run_id, RESPONSIBLE_CHILD, "fetch_done",
        f"{RESPONSIBLE_CHILD} (username={result['usernames_used']}"
        + (", FALLBACK topik sbg username" if result["used_fallback_topic_as_username"] else "")
        + f"): {len(result['posts'])} post mentah"
        + (f", error: {result['error']}" if result.get("error") else ""),
    )
    return {"posts": result["posts"]}
