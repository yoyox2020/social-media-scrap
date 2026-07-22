"""Child "Crawler" generik (2026-07-22) -- jalankan SEMUA target curl
terdaftar utk 1 agent (agent_curl_targets), parse hasilnya jadi bentuk
video yg seragam. BUKAN hardcode ke agent_youtube02 lagi -- terima
`agent_name` apa pun, supaya agent BARU (platform lain atau child baru)
otomatis bisa dipakai cuma dgn DAFTAR curl target lewat dashboard,
TANPA kode Python baru.

Kalau curl target-nya pakai placeholder {{KEYWORD}} di url/header/body,
target itu dijalankan SEKALI PER keyword yg dibagi coordinator ke agent
ini (lihat resolve_placeholders di app/services/agent_curl_targets/
service.py). Target yg TIDAK pakai {{KEYWORD}} tetap jalan seperti
biasa (1x, keyword diabaikan) -- backward-compat penuh dgn target lama
spt "curl youtube news trending"."""
from __future__ import annotations

import json

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.youtube.api_client import fetch_comments_for_video, is_valid_video_id, looks_like_youtube_key
from app.domain.agent_curl_targets.models import AgentCurlTarget
from app.services.agent_curl_targets.service import execute_target, get_targets_for_agent
from app.services.agent_registry.service import get_key_for_agent


def _uses_keyword_placeholder(target: AgentCurlTarget) -> bool:
    for field in (target.url, target.headers, target.body):
        if field and "{{KEYWORD}}" in field:
            return True
    return False


def _extract_video_items(response_json: dict) -> list[dict]:
    """YouTube search.list balikin id.videoId (nested), videos.list
    balikin id sbg string langsung -- tangani dua-duanya. Item yg
    id-nya BUKAN format video ID YouTube asli (11 karakter valid)
    DIBUANG, bukan diterima asal ada nilainya -- mencegah data salah
    kalau curl target ternyata bukan endpoint video (mis. keliru
    tertaut ke channel/playlist)."""
    items = response_json.get("items", [])
    normalized = []
    for item in items:
        raw_id = item.get("id")
        video_id = raw_id.get("videoId") if isinstance(raw_id, dict) else raw_id
        if not is_valid_video_id(video_id):
            continue
        normalized.append({
            "id": video_id,
            "snippet": item.get("snippet", {}),
            "statistics": item.get("statistics", {}),
            "contentDetails": item.get("contentDetails", {}),
        })
    return normalized


async def _run_one(db: AsyncSession, target: AgentCurlTarget, keyword: str | None) -> tuple[list[dict], str | None]:
    result = await execute_target(db, target.id, keyword=keyword)
    if not result or not result.get("success"):
        return [], (result or {}).get("error", "unknown")
    try:
        parsed = json.loads(result["response_text"])
    except (ValueError, KeyError):
        return [], "response bukan JSON valid"
    return _extract_video_items(parsed), None


async def fetch_via_curl_targets(db: AsyncSession, agent_name: str, keywords: list[str] | None = None) -> dict:
    """Jalankan semua curl target milik `agent_name`, kumpulkan semua
    video yg berhasil di-parse. Target yg gagal (network error, JSON
    tidak sesuai format) DICATAT tapi tidak menggagalkan target lain --
    best effort. Kalau `keywords` diisi DAN target pakai {{KEYWORD}},
    target itu dijalankan 1x PER keyword (bukan cuma sekali)."""
    targets = await get_targets_for_agent(db, agent_name)
    if not targets:
        return {"success": True, "videos": [], "targets_run": 0, "targets_failed": 0, "errors": []}

    all_videos: list[dict] = []
    errors: list[dict] = []
    runs_attempted = 0
    failed_count = 0

    for target in targets:
        if keywords and _uses_keyword_placeholder(target):
            kw_list = keywords
        else:
            kw_list = [None]  # jalankan sekali, tanpa substitusi keyword

        for kw in kw_list:
            runs_attempted += 1
            videos, error = await _run_one(db, target, kw)
            if error:
                failed_count += 1
                errors.append({"target_name": target.name, "keyword": kw, "error": error})
            else:
                all_videos.extend(videos)

    # Ambil komentar JUGA utk video dari crawler (2026-07-22, permintaan
    # user) -- CUMA kalau agent ini (mis. agent_youtube02) KEBETULAN py
    # key YouTube Data API asli sendiri (bukan OpenRouter). Kalau
    # belum, video crawler tetap tanpa komentar spt sebelumnya (bukan
    # error, cuma kemampuan tambahan yg butuh key yg sesuai).
    key_info = await get_key_for_agent(db, agent_name)
    if key_info and looks_like_youtube_key(key_info.get("api_key")):
        api_key = key_info["api_key"]
        async with httpx.AsyncClient(timeout=30.0) as client:
            for v in all_videos:
                v["_comments"] = await fetch_comments_for_video(client, api_key, v["id"])
    else:
        for v in all_videos:
            v["_comments"] = []

    return {
        "success": True, "videos": all_videos,
        "targets_run": runs_attempted, "targets_failed": failed_count, "errors": errors,
    }
