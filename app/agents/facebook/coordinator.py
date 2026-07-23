"""agent_facebook (coordinator/parent) -- bagi keyword ke SEMUA child
aktif yg punya >=1 curl target, pola SAMA PERSIS dgn coordinator TikTok
(1 jalur data: curl/Apify, tidak ada split API-vs-curl spt YouTube)."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.facebook.crawler_client import fetch_via_curl_targets
from app.services.agent_curl_targets.service import get_targets_for_agent
from app.services.agent_registry.service import get_enabled_children

AGENT_NAME = "agent_facebook"


async def _discover_candidates(db: AsyncSession) -> list[str]:
    children = await get_enabled_children(db, AGENT_NAME)
    candidates = []
    for child in children:
        targets = await get_targets_for_agent(db, child)
        if targets:
            candidates.append(child)
    return candidates


def _distribute_keywords(keywords: list[dict], children: list[str]) -> dict[str, list[str]]:
    assignment: dict[str, list[str]] = {c: [] for c in children}
    for i, kw in enumerate(keywords):
        child = children[i % len(children)]
        assignment[child].append(kw["keyword"])
    return assignment


async def run_children(db: AsyncSession, run_id: uuid.UUID, keywords: list[dict]) -> dict:
    children = await _discover_candidates(db)
    if not children:
        await log_activity(
            db, run_id, AGENT_NAME, "no_candidates",
            "Tidak ada child agent_facebook yg aktif+punya curl target -- pipeline berhenti", level="warning",
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
        result = await fetch_via_curl_targets(db, child, keywords=kws)
        await log_activity(
            db, run_id, child, "fetch_done",
            f"{child} (keyword={kws}): {len(result['posts'])} post mentah"
            + (f", error: {[e['error'] for e in result['errors']]}" if result["errors"] else ""),
        )
        return child, result

    results = await asyncio.gather(*[_run_one_child(c, kws) for c, kws in assignment.items()])

    all_posts: list[dict] = []
    for _child, result in results:
        all_posts.extend(result["posts"])

    await log_activity(
        db, run_id, AGENT_NAME, "children_merged",
        f"Semua child selesai, total post mentah (blm dedupe): {len(all_posts)}",
    )
    return {"posts": all_posts}
