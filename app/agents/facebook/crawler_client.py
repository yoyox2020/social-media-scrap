"""Child "Crawler" Facebook (2026-07-24) -- pola SAMA PERSIS dgn
app/agents/tiktok/crawler_client.py (curl target manapun terdaftar utk
agent ini otomatis dipakai, TIDAK ada "API resmi" Facebook spt YouTube
Data API krn Graph API resmi diblokir utk page publik di luar milik
sendiri -- lihat riwayat project, [[project_phase_status]]).

Aktor: Apify `danek/facebook-search-ppr` ("Facebook Search Scraper",
4,1 JUTA total run lintas semua user Apify, dikonfirmasi LIVE masih
ada+aktif 2026-07-24) -- input `{"query": "<keyword>", "search_type":
"posts", "max_posts": N}`.

**PENTING -- BELUM PERNAH live-tested end-to-end** (2026-07-24): SEMUA
6 token Apify di pool (`third_party_apis`) kena "Monthly usage hard
limit exceeded" tepat saat mau verifikasi (kena kuota abis dari
pemakaian TikTok reply-enrichment sesi ini) -- TIDAK ada satupun
kredensial Apify tersisa utk dites. Bentuk respons item (field
`_extract_items`/`_normalize` di bawah) MASIH TEBAKAN berbasis pola
umum aktor scraper Facebook Apify lain (bukan verifikasi langsung ke
aktor INI) -- SENGAJA dibuat DEFENSIF (banyak nama field alternatif
dicoba) + validasi ketat (item tanpa id/text/author valid DIBUANG, bukan
dipaksa simpan) supaya kalaupun tebakan field meleset, hasilnya "0
tersimpan" yg JELAS terlihat gagal -- BUKAN data sampah yg keliatan
berhasil. `raw_data` SELALU disimpan mentah (beda dari kode lama yg
TIDAK pernah simpan ini, terbukti dari 50 post lama yg raw_data-nya
NULL) -- begitu run pertama beneran jalan (kuota Apify tersedia lagi),
tinggal cek 1 baris `raw_data` utk tau bentuk asli & perbaiki mapping
di bawah kalau meleset, TANPA perlu scrape ulang."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_curl_targets.models import AgentCurlTarget
from app.services.agent_curl_targets.service import execute_target, get_targets_for_agent


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_present(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _uses_keyword_placeholder(target: AgentCurlTarget) -> bool:
    for field in (target.url, target.headers, target.body):
        if field and "{{KEYWORD}}" in field:
            return True
    return False


def _parse_time(value) -> str | None:
    """Aktor Facebook bisa balikin unix timestamp (int) ATAU string ISO
    -- dicoba dua-duanya, None kalau gagal (bukan asal tebak tanggal)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return value  # simpan apa adanya drpd dibuang -- struktur_data.py yg validasi akhir
    return None


def _normalize_item(item: dict) -> dict | None:
    """Konversi 1 item mentah aktor -> bentuk internal SAMA dgn TikTok
    (id/text/author/url/metrics/published_at/raw_data) spy
    struktur_data.py Facebook bisa pola sama persis. BELUM diverifikasi
    live -- lihat docstring modul."""
    post_id = _first_present(item, "post_id", "postId", "id")
    if post_id is None:
        return None
    post_id = str(post_id)

    text = _first_present(item, "text", "message", "content", "caption") or ""
    user = item.get("user") or item.get("author") or item.get("page") or {}
    if not isinstance(user, dict):
        user = {}
    author_name = _first_present(user, "name", "username", "pageName") \
        or _first_present(item, "pageName", "authorName", "page_name") or ""

    if not text and not author_name:
        return None

    url = _first_present(item, "url", "postUrl", "topLevelUrl", "link", "permalink") or ""
    followers = _first_present(user, "followers", "pageFollowers", "fan_count") \
        or _first_present(item, "pageFollowers", "followers")
    likes = _first_present(item, "likes", "likesCount", "reactionCount", "reactions_count") or 0
    comments = _first_present(item, "comments", "commentsCount", "comments_count") or 0
    shares = _first_present(item, "shares", "sharesCount", "shares_count") or 0
    time_raw = _first_present(item, "time", "timestamp", "date_posted", "date", "publishedAt")

    return {
        "external_id": post_id,
        "content": text,
        "author": author_name,
        "author_followers": _safe_int(followers) if followers is not None else None,
        "url": url,
        "metrics": {
            "views": 0,  # Facebook TIDAK expose view count publik -- konsisten dgn 50 post lama
            "likes": _safe_int(likes),
            "comments": _safe_int(comments),
            "shares": _safe_int(shares),
        },
        "published_at_raw": _parse_time(time_raw),
        "raw_data": item,
    }


def _extract_items(response_json) -> list[dict]:
    if isinstance(response_json, list):
        items = response_json
    elif isinstance(response_json, dict):
        items = response_json.get("items") or response_json.get("results") or response_json.get("data") or []
        if not isinstance(items, list):
            items = []
    else:
        items = []

    normalized = [_normalize_item(it) for it in items if isinstance(it, dict)]
    return [n for n in normalized if n is not None]


async def _run_one(db: AsyncSession, target: AgentCurlTarget, keyword: str | None) -> tuple[list[dict], str | None]:
    result = await execute_target(db, target.id, keyword=keyword)
    if not result or not result.get("success"):
        return [], (result or {}).get("error", "unknown")
    import json
    try:
        parsed = json.loads(result["response_text"])
    except (ValueError, KeyError):
        return [], "response bukan JSON valid"
    # execute_target() balikin success=True asal DAPAT respons HTTP
    # (walau status 401/402/403/429 stlh semua token rotasi dicoba+gagal)
    # -- badan respons Apify utk error itu JSON VALID `{"error":{...}}`,
    # tanpa cek ini bakal kebaca sbg "0 post" alih2 kegagalan asli
    # (ditemukan nyata 2026-07-24 lewat smoke test: kuota Apify exhausted
    # jadi tidak keliatan di `errors`, cuma "0 post mentah" tanpa alasan).
    if isinstance(parsed, dict) and "error" in parsed and not any(
        k in parsed for k in ("items", "results", "data")
    ):
        return [], f"Apify error: {parsed['error']}"
    return _extract_items(parsed), None


async def fetch_via_curl_targets(db: AsyncSession, agent_name: str, keywords: list[str] | None = None) -> dict:
    """Pola SAMA PERSIS dgn TikTok -- jalankan semua curl target milik
    `agent_name`, kumpulkan semua post yg berhasil di-parse."""
    targets = await get_targets_for_agent(db, agent_name)
    if not targets:
        return {"success": True, "posts": [], "targets_run": 0, "targets_failed": 0, "errors": []}

    all_posts: list[dict] = []
    errors: list[dict] = []
    runs_attempted = 0
    failed_count = 0

    for target in targets:
        kw_list = keywords if (keywords and _uses_keyword_placeholder(target)) else [None]
        for kw in kw_list:
            runs_attempted += 1
            posts, error = await _run_one(db, target, kw)
            if error:
                failed_count += 1
                errors.append({"target_name": target.name, "keyword": kw, "error": error})
            else:
                all_posts.extend(posts)

    return {
        "success": True, "posts": all_posts,
        "targets_run": runs_attempted, "targets_failed": failed_count, "errors": errors,
    }
