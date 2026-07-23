"""Child "Crawler" TikTok (2026-07-23) -- SEKARANG 2 provider (Apify +
EnsembleData, permintaan user "tambah lebih banyak akun apify dan
ensemble"), curl target manapun terdaftar utk agent ini otomatis
dipakai. TIDAK ada "API resmi" TikTok spt YouTube Data API, jadi TIDAK
ada api_client.py terpisah -- semua anak (agent_tiktok01..05) kerjanya
sama, cuma via curl target masing2.

DUA bentuk respons BEDA TOTAL, keduanya DIKONVERSI ke SATU bentuk
internal (bentuk Apify) di _extract_items() supaya struktur_data.py
TIDAK PERLU tahu bedanya:
- Apify: array JSON langsung, field `id` (digit), `playCount`/
  `diggCount`/`commentCount`/`shareCount`, `authorMeta.name`/`fans`,
  `videoMeta.coverUrl`, `webVideoUrl`, `createTimeISO`.
- EnsembleData: {"data": {"data": [...aweme items...]}} -- item aweme
  asli TikTok (field `aweme_id`, `desc`, `create_time`,
  `statistics.{digg_count,comment_count,play_count,share_count}`,
  `author.{unique_id,nickname,follower_count}`,
  `video.cover.url_list`), SEMUA diverifikasi LIVE 2026-07-23 lewat
  endpoint `/tt/hashtag/posts` (BUKAN tebakan).

  PENTING -- `/tt/keyword/posts` (endpoint keyword-search yg dulu
  dipakai project ini, lihat riwayat git
  app/integrations/tiktok/connector.py commit a26487c) SEKARANG 404,
  sudah tidak ada di API EnsembleData (dicek live, bukan asumsi).
  Diganti pakai `/tt/hashtag/posts?name=<keyword>` yg TERBUKTI jalan --
  konsekuensinya: keyword FRASA (mis. "rupiah lemah", ada spasi) HASIL-
  NYA BISA KOSONG krn hashtag TikTok tidak ada spasi, beda dari
  pencarian keyword bebas. Ini keterbatasan NYATA dari sisi API-nya,
  bukan bug di kode ini.

  TIDAK ada commentsDatasetUrl -- EnsembleData butuh panggilan TERPISAH
  per post (/tt/post/comments) yg BELUM diimplementasi (dicatat sbg
  keterbatasan, video EnsembleData _comments=[] apa adanya, bukan
  disembunyikan sbg "berhasil")."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_curl_targets.models import AgentCurlTarget
from app.services.agent_curl_targets.service import execute_target, get_targets_for_agent


def is_valid_tiktok_id(value) -> bool:
    """ID video TikTok SELALU digit semua, biasanya 18-19 karakter --
    SATU sumber kebenaran spy item yg id-nya bukan video asli (mis.
    hasil parsing keliru) tidak ikut kesimpan, pola sama dgn
    is_valid_video_id() YouTube (app/agents/youtube/api_client.py).
    Dipakai utk KEDUA bentuk (Apify `id`, EnsembleData `aweme_id`)."""
    return isinstance(value, str) and value.isdigit() and 5 <= len(value) <= 25


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _uses_keyword_placeholder(target: AgentCurlTarget) -> bool:
    for field in (target.url, target.headers, target.body):
        if field and "{{KEYWORD}}" in field:
            return True
    return False


def _convert_ensembledata_item(item: dict) -> dict | None:
    """aweme item (EnsembleData) -> bentuk internal SAMA dgn item Apify.
    Field mapping diverifikasi dari kode historis project ini yg PERNAH
    beneran dipakai (bukan tebakan) -- lihat docstring modul."""
    aweme_id = str(item.get("aweme_id") or "")
    if not is_valid_tiktok_id(aweme_id):
        return None
    author = item.get("author") or {}
    stats = item.get("statistics") or {}
    username = author.get("unique_id") or author.get("uniqueId") or ""
    create_time = item.get("create_time")
    create_time_iso = None
    if create_time:
        try:
            create_time_iso = datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            create_time_iso = None
    cover_list = ((item.get("video") or {}).get("cover") or {}).get("url_list") or []

    return {
        "id": aweme_id,
        "text": item.get("desc") or "",
        "authorMeta": {
            "name": username, "nickName": author.get("nickname", ""),
            # follower_count TIDAK terverifikasi ada di respons EnsembleData
            # (kode historis project ini tidak pernah memakainya) -- kalau
            # kosong, authority_score fallback ke default 40.0 di
            # struktur_data.py (SAMA persis pola YouTube), bukan error.
            "fans": _safe_int(author.get("follower_count")),
        },
        "webVideoUrl": f"https://www.tiktok.com/@{username}/video/{aweme_id}" if username else "",
        "createTimeISO": create_time_iso,
        "diggCount": _safe_int(stats.get("digg_count")),
        "playCount": _safe_int(stats.get("play_count")),
        "commentCount": _safe_int(stats.get("comment_count")),
        "shareCount": _safe_int(stats.get("share_count")),
        "videoMeta": {"coverUrl": cover_list[0] if cover_list else None},
        "commentsDatasetUrl": None,
    }


def _extract_items(response_json) -> list[dict]:
    if isinstance(response_json, list):
        # Bentuk Apify: array langsung.
        return [item for item in response_json if isinstance(item, dict) and is_valid_tiktok_id(item.get("id"))]

    if isinstance(response_json, dict):
        # EnsembleData /tt/hashtag/posts: {"data": {"data": [...aweme...]}}
        # (verified live 2026-07-23, BUKAN {"data":{"aweme_list":[...]}}
        # spt asumsi awal dari kode historis -- API-nya sudah berubah).
        outer = response_json.get("data")
        aweme_list = outer.get("data") if isinstance(outer, dict) else None
        if isinstance(aweme_list, list):
            converted = [_convert_ensembledata_item(it) for it in aweme_list if isinstance(it, dict)]
            return [c for c in converted if c is not None]

        items = response_json.get("items", [])
        return [item for item in items if isinstance(item, dict) and is_valid_tiktok_id(item.get("id"))]

    return []


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
