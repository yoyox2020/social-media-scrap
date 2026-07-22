"""agent_youtube -- parent/Coordinator (2026-07-22). Terima keyword dari
agent_search, BAGI keyword-keyword itu ke SEMUA child "API" yg AKTIF
(enabled=True) DAN punya key YouTube Data API asli (format "AIza...")
-- bukan cuma agent_youtube01 selalu. agent_youtube02 DIKECUALIKAN dari
pembagian ini krn perannya tetap Crawler (jalankan curl target
terdaftar, bukan search by keyword). Semua child (API manapun + 02)
jalan PARALEL, hasil digabung, kirim ke agent-struktur-data.

Pola pembagian INI GENERIK -- kalau platform lain (facebook, instagram,
dst) nanti dibuatkan coordinator serupa, logika "bagi N keyword ke M
child aktif yg punya key" bisa dipakai ulang persis sama, cuma ganti
parent_agent_name + crawler_agent_name."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.youtube import api_client, crawler_client
from app.services.agent_registry.service import get_enabled_children, get_key_for_agent

AGENT_NAME = "agent_youtube"
CRAWLER_AGENT = "agent_youtube02"


async def _get_valid_api_children(db: AsyncSession) -> list[str]:
    """Child aktif (enabled=True) milik agent_youtube, DIKECUALIKAN
    agent_youtube02 (peran tetap Crawler), DISARING cuma yg py key
    berformat YouTube Data API asli (bukan OpenRouter/kosong)."""
    children = await get_enabled_children(db, AGENT_NAME)
    candidates = [c for c in children if c != CRAWLER_AGENT]
    valid = []
    for name in candidates:
        key_info = await get_key_for_agent(db, name)
        if key_info and api_client.looks_like_youtube_key(key_info.get("api_key")):
            valid.append(name)
    return valid


def _distribute_keywords(keywords: list[str], children: list[str]) -> dict[str, list[str]]:
    """Bagi rata keyword ke child secara round-robin. Kalau child lebih
    banyak drpd keyword, sebagian child dapat 0 keyword (tidak
    dipanggil). Kalau keyword lebih banyak drpd child, 1 child bisa
    dapat lebih dari 1 keyword (dijalankan sekuensial utk child itu)."""
    assignment: dict[str, list[str]] = {c: [] for c in children}
    for i, kw in enumerate(keywords):
        child = children[i % len(children)]
        assignment[child].append(kw)
    return assignment


async def _run_child_api(db: AsyncSession, agent_name: str, kws: list[str], max_results: int) -> dict:
    all_videos: list[dict] = []
    all_channels: dict = {}
    errors: list[str] = []
    for kw in kws:
        result = await api_client.fetch_videos_by_keyword(db, kw, agent_name=agent_name, max_results=max_results)
        if result.get("success"):
            all_videos.extend(result.get("videos", []))
            all_channels.update(result.get("channels", {}))
        else:
            errors.append(f"{kw}: {result.get('error')}")
    return {"success": not errors or bool(all_videos), "agent_name": agent_name, "keywords": kws, "videos": all_videos, "channels": all_channels, "errors": errors}


async def run_children(db: AsyncSession, run_id: uuid.UUID, keywords: list[dict], max_results: int = 15) -> dict:
    keyword_list = [k["keyword"] for k in keywords]
    valid_children = await _get_valid_api_children(db)

    if not valid_children:
        await log_activity(
            db, run_id, AGENT_NAME, "no_valid_children",
            "Tidak ada child agent_youtube yg aktif+punya key YouTube Data API asli -- pencarian API dilewati",
            level="warning",
        )
        assignment = {}
    else:
        assignment = _distribute_keywords(keyword_list, valid_children)
        assignment = {k: v for k, v in assignment.items() if v}
        await log_activity(
            db, run_id, AGENT_NAME, "dispatch_children",
            f"Bagi {len(keyword_list)} keyword ke {len(assignment)} child aktif: "
            + ", ".join(f"{name}={kws}" for name, kws in assignment.items())
            + f" + agent_youtube02 (Crawler) -- semua jalan paralel",
        )

    api_tasks = [_run_child_api(db, name, kws, max_results) for name, kws in assignment.items()]
    results = await asyncio.gather(*api_tasks, crawler_client.fetch_via_curl_targets(db), return_exceptions=True)
    api_results = results[:-1]
    crawler_result = results[-1]

    all_api_videos: list[dict] = []
    all_api_channels: dict = {}
    for r in api_results:
        if isinstance(r, Exception):
            await log_activity(db, run_id, "agent_youtube_child", "fetch_error", f"exception: {r}", level="error")
            continue
        level = "info" if r.get("success") else "error"
        await log_activity(
            db, run_id, r["agent_name"], "fetch_done",
            f"{r['agent_name']} (keyword={r['keywords']}): {len(r['videos'])} video mentah"
            + (f", error: {r['errors']}" if r["errors"] else ""),
            level=level,
        )
        all_api_videos.extend(r["videos"])
        all_api_channels.update(r["channels"])

    if isinstance(crawler_result, Exception):
        await log_activity(db, run_id, CRAWLER_AGENT, "fetch_error", f"agent_youtube02 exception: {crawler_result}", level="error")
        crawler_result = {"success": False, "videos": [], "targets_run": 0, "targets_failed": 0, "errors": []}
    else:
        await log_activity(
            db, run_id, CRAWLER_AGENT, "fetch_done",
            f"agent_youtube02: {crawler_result.get('targets_run', 0)} target dijalankan, "
            f"{crawler_result.get('targets_failed', 0)} gagal, {len(crawler_result.get('videos', []))} video mentah",
            details={"errors": crawler_result.get("errors")} if crawler_result.get("errors") else None,
        )

    await log_activity(
        db, run_id, AGENT_NAME, "children_merged",
        f"Semua child selesai, total video mentah (blm dedupe): "
        f"{len(all_api_videos) + len(crawler_result.get('videos', []))}",
    )

    return {
        "api_videos": all_api_videos,
        "api_channels": all_api_channels,
        "crawler_videos": crawler_result.get("videos", []),
        "crawler_targets_run": crawler_result.get("targets_run", 0),
        "crawler_targets_failed": crawler_result.get("targets_failed", 0),
    }
