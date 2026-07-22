"""Child "Crawler" TikTok (2026-07-23) -- SATU-SATUNYA jalur ambil data
TikTok, via curl target Apify (`clockworks/tiktok-scraper`,
`searchQueries` mode, verified live sebelum ditulis -- lihat riset
curl sebelumnya). TIDAK ada "API resmi" TikTok spt YouTube Data API,
jadi TIDAK ada api_client.py terpisah -- semua anak (agent_tiktok01..05)
kerjanya sama, cuma via curl target masing2 (pola generik yg SAMA dgn
crawler_client.py YouTube, TANPA hardcode ke 1 agent tertentu).

Bentuk respons Apify BEDA dari YouTube: array JSON langsung (bukan
{"items":[...]}), field `id` numerik string (bukan 11-char), thumbnail
di `videoMeta.coverUrl`, views di `playCount` (bukan `viewCount`)."""
from __future__ import annotations

import json

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_curl_targets.models import AgentCurlTarget
from app.services.agent_curl_targets.service import execute_target, get_targets_for_agent


def is_valid_tiktok_id(value) -> bool:
    """ID video TikTok SELALU digit semua, biasanya 18-19 karakter --
    SATU sumber kebenaran spy item yg id-nya bukan video asli (mis.
    hasil parsing keliru) tidak ikut kesimpan, pola sama dgn
    is_valid_video_id() YouTube (app/agents/youtube/api_client.py)."""
    return isinstance(value, str) and value.isdigit() and 5 <= len(value) <= 25


def _uses_keyword_placeholder(target: AgentCurlTarget) -> bool:
    for field in (target.url, target.headers, target.body):
        if field and "{{KEYWORD}}" in field:
            return True
    return False


def _extract_items(response_json) -> list[dict]:
    items = response_json if isinstance(response_json, list) else response_json.get("items", [])
    normalized = []
    for item in items:
        if not isinstance(item, dict) or not is_valid_tiktok_id(item.get("id")):
            continue
        normalized.append(item)
    return normalized


async def _fetch_comments_dataset(client: httpx.AsyncClient, dataset_url: str) -> list[dict]:
    """Semua item dlm 1 run Apify BERBAGI 1 commentsDatasetUrl yg SAMA
    (bukan per-video) -- verified live sebelumnya. Fetch SEKALI per
    target run, bukan per item (hindari panggilan HTTP redundan)."""
    try:
        resp = await client.get(dataset_url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _run_one(db: AsyncSession, target: AgentCurlTarget, keyword: str | None) -> tuple[list[dict], str | None]:
    result = await execute_target(db, target.id, keyword=keyword)
    if not result or not result.get("success"):
        return [], (result or {}).get("error", "unknown")
    try:
        parsed = json.loads(result["response_text"])
    except (ValueError, KeyError):
        return [], "response bukan JSON valid"
    items = _extract_items(parsed)

    # commentsDatasetUrl (kalau ada, krn commentsPerPost>0 di body target)
    # dibagi 1x utk semua item run ini, lalu dicocokkan balik ke tiap
    # item via webVideoUrl -- validasi ketat spt YouTube, BUKAN asumsi
    # comment pertama otomatis milik video pertama.
    dataset_url = next((it.get("commentsDatasetUrl") for it in items if it.get("commentsDatasetUrl")), None)
    if dataset_url:
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_comments = await _fetch_comments_dataset(client, dataset_url)
        for it in items:
            web_url = it.get("webVideoUrl")
            it["_comments"] = [c for c in all_comments if c.get("videoWebUrl") == web_url] if web_url else []
    else:
        for it in items:
            it["_comments"] = []

    return items, None


async def fetch_via_curl_targets(db: AsyncSession, agent_name: str, keywords: list[str] | None = None) -> dict:
    """Jalankan semua curl target milik `agent_name`, kumpulkan semua
    video TikTok yg berhasil di-parse -- pola SAMA PERSIS dgn versi
    YouTube (best-effort per target, 1x per keyword kalau target pakai
    {{KEYWORD}})."""
    targets = await get_targets_for_agent(db, agent_name)
    if not targets:
        return {"success": True, "videos": [], "targets_run": 0, "targets_failed": 0, "errors": []}

    all_videos: list[dict] = []
    errors: list[dict] = []
    runs_attempted = 0
    failed_count = 0

    for target in targets:
        kw_list = keywords if (keywords and _uses_keyword_placeholder(target)) else [None]
        for kw in kw_list:
            runs_attempted += 1
            videos, error = await _run_one(db, target, kw)
            if error:
                failed_count += 1
                errors.append({"target_name": target.name, "keyword": kw, "error": error})
            else:
                all_videos.extend(videos)

    return {
        "success": True, "videos": all_videos,
        "targets_run": runs_attempted, "targets_failed": failed_count, "errors": errors,
    }
