"""Child "API" YouTube Data API v3 (2026-07-22) -- BISA dipanggil oleh
child mana pun (agent_youtube01, atau child lain yg dibagi kerjanya via
distribusi keyword), bukan cuma agent_youtube01 hardcode. Key SELALU
diambil ulang dari agent_registry/third_party_apis saat dipanggil
(lihat get_key_for_agent), tidak pernah hardcode -- kalau user ganti
key lewat dashboard, run berikutnya otomatis pakai yg baru.

MVP (versi sederhana, sesuai permintaan user "yang penting bisa jalan
dulu"): video+channel+statistics+comments. Caption/transcript/live/
playlist BELUM diimplementasi (butuh endpoint terpisah + kuota lebih
besar) -- dicatat sbg keterbatasan, bukan diam-diam dilewati.

Komentar (2026-07-22, permintaan user "harusnya unlimited"): YouTube
`commentThreads.list` maksimal 100/panggilan (BUKAN 20 spt versi
sebelumnya -- itu pilihan sendiri, bukan batas YouTube). Utk lebih dari
100, dipaginasi pakai `nextPageToken` sampai `MAX_COMMENTS_PER_VIDEO`
(500 = 5 panggilan) -- BUKAN benar2 tanpa batas krn video viral bisa
py 100rb+ komentar, kalau dipaginasi semua 1 video saja bisa makan
ratusan panggilan & bikin 1 run pipeline jalan berjam-jam. 500 dipilih
sbg kompromi "jauh lebih banyak drpd 20" TAPI tetap terkendali."""
from __future__ import annotations

import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent_registry.service import get_key_for_agent

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_COMMENTS_PER_VIDEO = 500
COMMENTS_PAGE_SIZE = 100

# Video ID YouTube SELALU 11 karakter [A-Za-z0-9_-] (2026-07-22,
# permintaan user "validasi ketat, jangan asal cabut komentar, krn
# mengakibatkan data tidak sesuai") -- SATU sumber kebenaran, dipakai
# di sini (search.list) MAUPUN di crawler_client.py (curl target),
# supaya post/comment TIDAK PERNAH dibangun dari id yg formatnya salah.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_valid_video_id(value) -> bool:
    return isinstance(value, str) and bool(VIDEO_ID_RE.match(value))


def looks_like_youtube_key(api_key: str | None) -> bool:
    """Heuristik: key YouTube Data API asli SELALU mulai 'AIza' (format
    Google API key) -- beda dari key OpenRouter ('sk-or-v1-...'). Dipakai
    utk saring child mana yg BENERAN punya key YouTube (bukan cuma key
    LLM yg kebetulan tersimpan di kolom yg sama)."""
    return bool(api_key) and api_key.startswith("AIza")


async def get_youtube_api_key(db: AsyncSession) -> str | None:
    """SATU titik pengambilan key YouTube Data API utk refresh/completeness/
    comment_backfill (2026-07-24) -- SEBELUMNYA ketiganya hardcode
    `get_key_for_agent(db, "agent_youtube01")` doang, TIDAK PERNAH pakai
    2 key YouTube Data API LAIN yg SUDAH terdaftar di katalog
    third_party_apis (agent_youtube02/03) -- kapasitas kuota nganggur
    ditemukan saat audit "setiap platform py 1 group rotasi" 2026-07-24.
    Sekarang: rotasi grup platform_group="youtube" DULU (otomatis pakai
    key manapun yg available, termasuk yg BARU ditambah user nanti),
    fallback ke agent_youtube01 kalau grup kosong (kompatibel dgn setup
    lama)."""
    from app.services.third_party_apis.service import get_next_available_key

    key_entry = await get_next_available_key(db, "YouTube Data API v3", platform_group="youtube")
    if key_entry and looks_like_youtube_key(key_entry.api_key):
        return key_entry.api_key

    key_info = await get_key_for_agent(db, "agent_youtube01")
    if key_info and looks_like_youtube_key(key_info.get("api_key")):
        return key_info["api_key"]
    return None


async def fetch_comments_for_video(
    client: httpx.AsyncClient, api_key: str, video_id: str, max_comments: int = MAX_COMMENTS_PER_VIDEO,
) -> list[dict]:
    """Ambil komentar 1 video, DIPAGINASI (nextPageToken) sampai
    `max_comments` atau habis -- dipakai baik oleh agent_youtube01
    (child API) maupun crawler (agent_youtube02, kalau dia py key
    YouTube asli sendiri). Best-effort: video yg comment-nya
    dimatikan/private balikin list kosong, tidak melempar exception.

    Validasi ketat (2026-07-22, permintaan user "jangan asal cabut
    komentar, krn mengakibatkan data tidak sesuai"): tiap item balikan
    YouTube DICEK ULANG `snippet.videoId`-nya SAMA PERSIS dgn
    `video_id` yg diminta -- item yg TIDAK cocok DIBUANG (bukan asumsi
    otomatis benar), sbg lapis pertahanan kedua di luar filter format
    ID di crawler_client.py."""
    comments: list[dict] = []
    page_token = None
    try:
        while len(comments) < max_comments:
            params = {
                "part": "snippet", "videoId": video_id,
                "maxResults": min(COMMENTS_PAGE_SIZE, max_comments - len(comments)),
                "order": "relevance", "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"{YOUTUBE_API_BASE}/commentThreads", params=params)
            if resp.status_code != 200:
                break
            data = resp.json()
            for item in data.get("items", []):
                if item.get("snippet", {}).get("videoId") == video_id:
                    comments.append(item)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        pass
    return comments


async def fetch_videos_by_keyword(
    db: AsyncSession, keyword: str, agent_name: str, max_results: int = 15, region_code: str = "ID",
) -> dict:
    """Cari video by keyword (search.list) -> ambil detail lengkap
    (videos.list part=snippet,statistics,contentDetails) -> ambil channel
    (channels.list) -> ambil komentar (dipaginasi, lihat
    fetch_comments_for_video). `agent_name` menentukan key SIAPA yg
    dipakai -- BUKAN selalu agent_youtube01."""
    key_info = await get_key_for_agent(db, agent_name)
    if not key_info or not key_info.get("api_key"):
        return {"success": False, "error": f"Agent '{agent_name}' belum punya key aktif", "videos": [], "channels": {}}
    if not looks_like_youtube_key(key_info["api_key"]):
        return {"success": False, "error": f"Agent '{agent_name}' key-nya bukan format YouTube Data API (AIza...)", "videos": [], "channels": {}}

    api_key = key_info["api_key"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        search_resp = await client.get(f"{YOUTUBE_API_BASE}/search", params={
            "part": "snippet", "type": "video", "q": keyword, "order": "date",
            "regionCode": region_code, "maxResults": max_results, "key": api_key,
        })
        if search_resp.status_code != 200:
            return {"success": False, "error": f"search.list gagal HTTP {search_resp.status_code}: {search_resp.text[:300]}", "videos": [], "channels": {}}
        search_data = search_resp.json()
        video_ids = [
            item["id"]["videoId"] for item in search_data.get("items", [])
            if is_valid_video_id(item.get("id", {}).get("videoId"))
        ]

        if not video_ids:
            return {"success": True, "videos": [], "channels": {}}

        videos_resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params={
            "part": "snippet,statistics,contentDetails", "id": ",".join(video_ids), "key": api_key,
        })
        if videos_resp.status_code != 200:
            return {"success": False, "error": f"videos.list gagal HTTP {videos_resp.status_code}: {videos_resp.text[:300]}", "videos": [], "channels": {}}
        # Filter cuma video yg BENAR-BENAR kita minta (bukan asumsi
        # otomatis benar) -- lapis pertahanan tambahan.
        requested_ids = set(video_ids)
        videos_data = [v for v in videos_resp.json().get("items", []) if v.get("id") in requested_ids]

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
            v["_comments"] = await fetch_comments_for_video(client, api_key, v["id"])

        return {"success": True, "videos": videos_data, "channels": channels_by_id}
