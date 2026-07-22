"""Child "API" YouTube Data API v3 (2026-07-22) -- BISA dipanggil oleh
child mana pun (agent_youtube01, atau child lain yg dibagi kerjanya via
distribusi keyword), bukan cuma agent_youtube01 hardcode. Key SELALU
diambil ulang dari agent_registry/third_party_apis saat dipanggil
(lihat get_key_for_agent), tidak pernah hardcode -- kalau user ganti
key lewat dashboard, run berikutnya otomatis pakai yg baru.

MVP (versi sederhana, sesuai permintaan user "yang penting bisa jalan
dulu"): video+channel+statistics+comments. Caption/transcript/live/
playlist BELUM diimplementasi (butuh endpoint terpisah + kuota lebih
besar) -- dicatat sbg keterbatasan, bukan diam-diam dilewati."""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_registry.service import get_key_for_agent

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def looks_like_youtube_key(api_key: str | None) -> bool:
    """Heuristik: key YouTube Data API asli SELALU mulai 'AIza' (format
    Google API key) -- beda dari key OpenRouter ('sk-or-v1-...'). Dipakai
    utk saring child mana yg BENERAN punya key YouTube (bukan cuma key
    LLM yg kebetulan tersimpan di kolom yg sama)."""
    return bool(api_key) and api_key.startswith("AIza")


async def fetch_videos_by_keyword(
    db: AsyncSession, keyword: str, agent_name: str, max_results: int = 15, region_code: str = "ID",
) -> dict:
    """Cari video by keyword (search.list) -> ambil detail lengkap
    (videos.list part=snippet,statistics,contentDetails) -> ambil channel
    (channels.list) -> ambil sebagian comment (commentThreads.list, best
    effort -- video yg comment-nya dimatikan/private akan gagal, itu
    normal, tidak menggagalkan keseluruhan). `agent_name` menentukan key
    SIAPA yg dipakai -- BUKAN selalu agent_youtube01."""
    key_info = await get_key_for_agent(db, agent_name)
    if not key_info or not key_info.get("api_key"):
        return {"success": False, "error": f"Agent '{agent_name}' belum punya key aktif", "videos": [], "channels": {}}
    if not looks_like_youtube_key(key_info["api_key"]):
        return {"success": False, "error": f"Agent '{agent_name}' key-nya bukan format YouTube Data API (AIza...)", "videos": [], "channels": {}}

    api_key = key_info["api_key"]

    async with httpx.AsyncClient(timeout=20.0) as client:
        search_resp = await client.get(f"{YOUTUBE_API_BASE}/search", params={
            "part": "snippet", "type": "video", "q": keyword, "order": "date",
            "regionCode": region_code, "maxResults": max_results, "key": api_key,
        })
        if search_resp.status_code != 200:
            return {"success": False, "error": f"search.list gagal HTTP {search_resp.status_code}: {search_resp.text[:300]}", "videos": [], "channels": {}}
        search_data = search_resp.json()
        video_ids = [item["id"]["videoId"] for item in search_data.get("items", []) if item.get("id", {}).get("videoId")]

        if not video_ids:
            return {"success": True, "videos": [], "channels": {}}

        videos_resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params={
            "part": "snippet,statistics,contentDetails", "id": ",".join(video_ids), "key": api_key,
        })
        if videos_resp.status_code != 200:
            return {"success": False, "error": f"videos.list gagal HTTP {videos_resp.status_code}: {videos_resp.text[:300]}", "videos": [], "channels": {}}
        videos_data = videos_resp.json().get("items", [])

        channel_ids = list({v["snippet"]["channelId"] for v in videos_data if v.get("snippet", {}).get("channelId")})
        channels_by_id: dict = {}
        if channel_ids:
            channels_resp = await client.get(f"{YOUTUBE_API_BASE}/channels", params={
                "part": "snippet,statistics", "id": ",".join(channel_ids), "key": api_key,
            })
            if channels_resp.status_code == 200:
                for ch in channels_resp.json().get("items", []):
                    channels_by_id[ch["id"]] = ch

        for v in videos_data:
            video_id = v["id"]
            try:
                comments_resp = await client.get(f"{YOUTUBE_API_BASE}/commentThreads", params={
                    "part": "snippet", "videoId": video_id, "maxResults": 20, "order": "relevance", "key": api_key,
                })
                if comments_resp.status_code == 200:
                    v["_comments"] = comments_resp.json().get("items", [])
                else:
                    v["_comments"] = []
            except Exception:
                v["_comments"] = []

        return {"success": True, "videos": videos_data, "channels": channels_by_id}
