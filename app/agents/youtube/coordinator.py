"""agent_youtube -- parent/Coordinator (2026-07-22). Terima keyword dari
agent_search, JALANKAN child agent_youtube01 (API) + agent_youtube02
(Crawler/curl target) SECARA PARALEL, tunggu keduanya selesai, gabungkan
hasil mentah, kirim ke agent-struktur-data.

MVP: pakai keyword UTAMA saja (priority=1) utk agent_youtube01 supaya
kuota YouTube API terkendali -- agent_youtube02 keyword-agnostic
(jalankan curl target apa adanya, terlepas dari keyword)."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.agents.youtube import api_client, crawler_client

AGENT_NAME = "agent_youtube"


async def run_children(db: AsyncSession, run_id: uuid.UUID, keywords: list[dict]) -> dict:
    primary_keyword = next((k["keyword"] for k in keywords if k.get("priority") == 1), keywords[0]["keyword"] if keywords else "")

    await log_activity(
        db, run_id, AGENT_NAME, "dispatch_children",
        f"Memerintahkan agent_youtube01 (API, keyword='{primary_keyword}') + agent_youtube02 (Crawler) jalan paralel",
    )

    results = await asyncio.gather(
        api_client.fetch_videos_by_keyword(db, primary_keyword),
        crawler_client.fetch_via_curl_targets(db),
        return_exceptions=True,
    )
    api_result, crawler_result = results

    if isinstance(api_result, Exception):
        await log_activity(db, run_id, "agent_youtube01", "fetch_error", f"agent_youtube01 exception: {api_result}", level="error")
        api_result = {"success": False, "error": str(api_result), "videos": [], "channels": {}}
    else:
        level = "info" if api_result.get("success") else "error"
        await log_activity(
            db, run_id, "agent_youtube01", "fetch_done",
            f"agent_youtube01: {'berhasil' if api_result.get('success') else 'gagal'}, {len(api_result.get('videos', []))} video mentah",
            level=level, details={"error": api_result.get("error")} if not api_result.get("success") else None,
        )

    if isinstance(crawler_result, Exception):
        await log_activity(db, run_id, "agent_youtube02", "fetch_error", f"agent_youtube02 exception: {crawler_result}", level="error")
        crawler_result = {"success": False, "videos": [], "targets_run": 0, "targets_failed": 0, "errors": []}
    else:
        await log_activity(
            db, run_id, "agent_youtube02", "fetch_done",
            f"agent_youtube02: {crawler_result.get('targets_run', 0)} target dijalankan, "
            f"{crawler_result.get('targets_failed', 0)} gagal, {len(crawler_result.get('videos', []))} video mentah",
            details={"errors": crawler_result.get("errors")} if crawler_result.get("errors") else None,
        )

    await log_activity(
        db, run_id, AGENT_NAME, "children_merged",
        f"Kedua child selesai, total video mentah (blm dedupe): "
        f"{len(api_result.get('videos', [])) + len(crawler_result.get('videos', []))}",
    )

    return {
        "primary_keyword": primary_keyword,
        "api_videos": api_result.get("videos", []),
        "api_channels": api_result.get("channels", {}),
        "api_success": api_result.get("success", False),
        "api_error": api_result.get("error"),
        "crawler_videos": crawler_result.get("videos", []),
        "crawler_targets_run": crawler_result.get("targets_run", 0),
        "crawler_targets_failed": crawler_result.get("targets_failed", 0),
    }
