"""agent_news (coordinator/parent) -- distribusi keyword ke child aktif,
pola SAMA dgn Threads/Twitter (genuinely keyword-search via Firecrawl)."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.news.crawler_client import fetch_via_keywords
from app.services.agent_registry.service import get_enabled_children

AGENT_NAME = "agent_news"


def _distribute_keywords(keywords: list[dict], children: list[str]) -> dict[str, list[str]]:
    assignment: dict[str, list[str]] = {c: [] for c in children}
    for i, kw in enumerate(keywords):
        child = children[i % len(children)]
        assignment[child].append(kw["keyword"])
    return assignment


async def run_children(db: AsyncSession, run_id: uuid.UUID, keywords: list[dict]) -> dict:
    children = await get_enabled_children(db, AGENT_NAME)
    if not children:
        await log_activity(
            db, run_id, AGENT_NAME, "no_candidates",
            "Tidak ada child agent_news yg aktif -- pipeline berhenti", level="warning",
        )
        return {"posts": []}

    assignment = _distribute_keywords(keywords, children)
    await log_activity(
        db, run_id, AGENT_NAME, "dispatch_children",
        f"Bagi {len(keywords)} keyword ke {len(children)} child aktif: "
        + ", ".join(f"{c}={kws}" for c, kws in assignment.items() if kws),
    )

    async def _run_one_child(child: str, kws: list[str]) -> tuple[str, dict]:
        if not kws:
            return child, {"posts": []}
        result = await fetch_via_keywords(db, kws)
        await log_activity(
            db, run_id, child, "fetch_done",
            f"{child} (keyword={kws}): {len(result['posts'])} artikel"
            + (f", error: {[e['error'] for e in result['errors']]}" if result["errors"] else ""),
        )
        return child, result

    results = await asyncio.gather(*[_run_one_child(c, kws) for c, kws in assignment.items()])

    all_posts: list[dict] = []
    for _child, result in results:
        all_posts.extend(result["posts"])

    await log_activity(
        db, run_id, AGENT_NAME, "children_merged",
        f"Semua child selesai, total artikel mentah (blm dedupe): {len(all_posts)}",
    )
    return {"posts": all_posts}
