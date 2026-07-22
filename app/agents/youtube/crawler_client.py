"""agent_youtube02 -- child "Crawler": jalankan SEMUA target curl yg
terdaftar utk agent ini (agent_curl_targets, 2026-07-22), parse hasilnya
jadi bentuk video yg seragam. Curl target BEBAS diubah kapan saja lewat
dashboard tab "Target Curl" -- kode ini SELALU baca ulang daftar
terbaru tiap dipanggil, tidak pernah hardcode URL."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_curl_targets.service import execute_target, get_targets_for_agent

AGENT_NAME = "agent_youtube02"


def _extract_video_items(response_json: dict) -> list[dict]:
    """YouTube search.list balikin id.videoId (nested), videos.list
    balikin id sbg string langsung -- tangani dua-duanya."""
    items = response_json.get("items", [])
    normalized = []
    for item in items:
        raw_id = item.get("id")
        video_id = raw_id.get("videoId") if isinstance(raw_id, dict) else raw_id
        if not video_id:
            continue
        normalized.append({
            "id": video_id,
            "snippet": item.get("snippet", {}),
            "statistics": item.get("statistics", {}),
            "contentDetails": item.get("contentDetails", {}),
        })
    return normalized


async def fetch_via_curl_targets(db: AsyncSession) -> dict:
    """Jalankan semua curl target milik agent_youtube02, kumpulkan
    semua video yg berhasil di-parse. Target yg gagal (network error,
    JSON tidak sesuai format) DICATAT tapi tidak menggagalkan target
    lain -- best effort, konsisten dgn semangat "yang penting bisa
    jalan dulu"."""
    targets = await get_targets_for_agent(db, AGENT_NAME)
    if not targets:
        return {"success": True, "videos": [], "targets_run": 0, "targets_failed": 0, "errors": []}

    all_videos: list[dict] = []
    errors: list[dict] = []
    failed_count = 0

    for target in targets:
        result = await execute_target(db, target.id)
        if not result or not result.get("success"):
            failed_count += 1
            errors.append({"target_name": target.name, "error": (result or {}).get("error", "unknown")})
            continue
        try:
            parsed = json.loads(result["response_text"])
        except (ValueError, KeyError):
            failed_count += 1
            errors.append({"target_name": target.name, "error": "response bukan JSON valid"})
            continue
        videos = _extract_video_items(parsed)
        all_videos.extend(videos)

    return {
        "success": True, "videos": all_videos,
        "targets_run": len(targets), "targets_failed": failed_count, "errors": errors,
    }
