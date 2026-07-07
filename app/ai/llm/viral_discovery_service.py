"""
Viral discovery harian — AI menyapu berita + Instagram publik Indonesia untuk
menemukan topik yang BENAR-BENAR viral HARI INI (bukan satu keyword tertentu,
sapuan terbuka), hasilnya di-submit ke trend_recommendations (status=pending)
supaya diambil pipeline scrape Instagram yang SUDAH ADA (budget harian,
jadwal 09:00 WIB) — lihat docs/trend-recommendations.md.

Provider AI bisa diganti via .env TANPA ubah kode:
    AI_DISCOVERY_PROVIDER=anthropic | openai | ollama

CATATAN PENTING soal browsing per provider:
- "anthropic" (Claude): web_search BAWAAN, dieksekusi server-side oleh
  infrastruktur Anthropic sendiri (model "browsing sendiri").
- "ollama": browsing lewat tool `web_search` CUSTOM — auto-switch: Firecrawl
  dicoba dulu (FIRECRAWL_API_KEY), fallback ke Tavily (TAVILY_API_KEY) kalau
  Firecrawl gagal/limit/key kosong (lihat _web_search()). Model TIDAK bisa
  akses internet sendiri — kode di sini yang benar-benar eksekusi pencarian
  lalu kirim hasilnya balik ke model (pola function-calling standar). Kalau
  KEDUANYA kosong, fallback ke pengetahuan training model (bisa basi/salah,
  skor rendah).
- "openai": function calling saja, BELUM diberi tool web_search (belum
  diminta) — hasilnya dari pengetahuan training model.

Dipanggil oleh app/services/trend_recommendations/viral_discovery_scrape_service.py
(bukan file trend_scrape_service.py yang dibekukan) sebagai bagian dari task
Celery harian workers.viral_discovery.daily_scan.

Bukan HTTP call ke POST /trend-recommendations sendiri — hasilnya dikirim ke
submit_recommendations() langsung sebagai fungsi Python karena sudah dalam
proses yang sama.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from app.shared.config import settings

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_trend_topics"

_TOOL_DESCRIPTION = (
    "Submit topik/akun Instagram yang BENAR-BENAR viral hari ini, ditemukan lewat "
    "web search nyata. Panggil ini SETELAH menemukan data nyata — jangan mengarang "
    "topik atau username yang tidak terverifikasi."
)

_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "Daftar topik viral hari ini, tiap topik object dengan topic/score/related_accounts",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "related_accounts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "platform": {"type": "string"},
                                "username": {"type": "string"},
                            },
                            "required": ["platform", "username"],
                        },
                    },
                },
                "required": ["topic", "score", "related_accounts"],
            },
        },
    },
    "required": ["items"],
}

# Tool web_search — cuma dipakai provider Ollama (Claude punya web_search
# bawaan sendiri, lihat _find_via_anthropic). Model TIDAK mengeksekusi ini
# sendiri, cuma minta kita eksekusi lalu kirim hasilnya balik sebagai pesan.
_WEB_SEARCH_TOOL_NAME = "web_search"

_WEB_SEARCH_TOOL_DESCRIPTION = (
    "Cari informasi TERKINI di internet (via Tavily). Wajib dipakai untuk "
    "menemukan topik/akun yang viral HARI INI — jangan mengandalkan ingatan "
    "training data yang bisa basi."
)

_WEB_SEARCH_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Kata kunci pencarian"},
    },
    "required": ["query"],
}

MAX_ITERATIONS = 8

# Berapa kali model DIPAKSA coba web_search lagi kalau dia menyerah (jawab
# teks biasa tanpa tool_call) padahal belum pernah submit topik apa pun.
# qwen3:8b sering bilang "coba lagi dengan query lebih spesifik" tapi TIDAK
# benar-benar melakukannya — ini nudge otomatis supaya dia beneran mencoba.
# Naikkan angka ini kalau mau model lebih gigih (menambah waktu proses per +1).
FORCE_RETRY_LIMIT = 1


_SUPPORTED_PLATFORMS = {"instagram", "facebook", "tiktok"}

# ─────────────────────────────────────────────────────────────────────────────
# Ekstraksi kandidat akun dari URL literal di hasil web_search (lihat
# _extract_account_candidates below). qwen3:8b sering gagal mengenali ID
# numerik Facebook (mis. "100090312503944") sebagai akun yang valid kalau
# cuma disuruh baca teks bebas — jadi kode ini yang ekstrak duluan lalu
# sodorkan sebagai daftar pilihan eksplisit ke model.
#
# GAMPANG DIMODIFIKASI: kalau nanti ketemu pola URL Facebook/Instagram baru
# yang BUKAN akun (ikut lolos filter, jadi "kandidat" palsu), tinggal tambah
# ke set di bawah — tidak perlu ubah logic ekstraksinya.
# ─────────────────────────────────────────────────────────────────────────────
_FB_URL_RE = re.compile(r"facebook\.com/([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_IG_URL_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.\-]+)", re.IGNORECASE)
_TT_URL_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9_.\-]+)", re.IGNORECASE)

_FB_RESERVED_PATHS = {
    "watch", "groups", "share", "reel", "reels", "photo.php", "permalink.php",
    "story.php", "profile.php", "pages", "events", "marketplace", "gaming",
    "help", "policies", "ads", "business", "settings", "login.php", "plugins",
    "dialog", "hashtag", "search", "notifications", "messages", "live",
}
_IG_RESERVED_PATHS = {
    "p", "reel", "reels", "explore", "stories", "accounts", "direct", "tv",
    "about", "developer", "legal", "embed",
}
_TT_RESERVED_PATHS = {
    "video", "tag", "music", "discover", "live", "search", "explore",
    "upload", "following", "foryou", "trending",
}


def _extract_items(raw_items: list[dict], max_topics: int) -> list[dict]:
    found = []
    for item in raw_items[:max_topics]:
        accounts = [a for a in item.get("related_accounts", []) if a.get("platform") in _SUPPORTED_PLATFORMS]
        if accounts:
            found.append({**item, "related_accounts": accounts})
    return found


def _system_prompt(max_topics: int, has_web_search: bool) -> str:
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    browsing_note = (
        f"gunakan web_search untuk menemukan (query HARUS spesifik dan menyertakan "
        f"tanggal/bulan/tahun ini — {today} — bukan istilah generik seperti "
        f'"trending topics Indonesia" tanpa konteks waktu)'
        if has_web_search
        else "sebutkan (dari pengetahuanmu — CATATAN: kamu tidak punya akses internet, "
             "jadi ini mungkin bukan data hari ini yang sebenarnya, beri tahu itu di skor rendah)"
    )
    account_rule = (
        "WAJIB sertakan minimal SATU akun nyata — Instagram (platform='instagram'), "
        "Facebook (platform='facebook'), DAN/ATAU TikTok (platform='tiktok') — yang "
        "mendorong viralitasnya untuk tiap topik. Akun itu HARUS benar-benar "
        "disebutkan namanya di hasil web_search (judul/isi/URL) — JANGAN PERNAH "
        "mengarang atau menebak username yang tidak muncul eksplisit di hasil "
        "pencarian. Kalau hasil pencarian cuma bahas topik tanpa menyebut akun "
        "spesifik, cari lagi dengan query lain (query berbeda) sebelum menyerah "
        "pada topik itu."
        if has_web_search
        else
        "WAJIB sertakan minimal SATU akun nyata — Instagram (platform='instagram'), "
        "Facebook (platform='facebook'), DAN/ATAU TikTok (platform='tiktok') — yang "
        "mendorong viralitasnya untuk tiap topik."
    )
    return (
        f"Kamu adalah AI trend-analyst. Hari ini tanggal {today}. Tugasmu HARI INI: "
        f"{browsing_note} topik/isu yang BENAR-BENAR sedang viral di berita Indonesia "
        "dan media sosial publik (Instagram, Facebook, dan/atau TikTok) — bukan satu "
        "keyword tertentu, tapi sapuan terbuka (bisa politik, hiburan, olahraga, "
        f"produk viral, dll). {account_rule} Kalau tidak menemukan akun Instagram, "
        f"Facebook, MAUPUN TikTok sama sekali untuk suatu topik, jangan sertakan "
        f"topik itu. Maksimal {max_topics} topik, jangan mengarang untuk mencapai "
        "jumlah itu — kalau cuma menemukan lebih sedikit, submit yang nyata saja. "
        f"Setelah menemukan data nyata, panggil tool {_TOOL_NAME}."
    )


async def find_daily_viral_topics() -> list[dict]:
    """
    Sapuan terbuka harian untuk topik+akun Instagram yang viral hari ini.
    Provider dipilih dari settings.ai_discovery_provider (anthropic/openai/ollama).
    Return list item siap dipakai `TrendRecommendationItem`
    (topic/score/related_accounts) — HANYA yang punya minimal 1 akun Instagram.
    """
    provider = settings.ai_discovery_provider

    if provider == "anthropic":
        return await _find_via_anthropic()
    if provider == "openai":
        return await _find_via_openai()
    if provider == "ollama":
        return await _find_via_ollama()

    logger.warning("find_daily_viral_topics: ai_discovery_provider tidak dikenal: %s", provider)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Anthropic (Claude) — satu-satunya yang punya web_search bawaan
# ─────────────────────────────────────────────────────────────────────────────

async def _find_via_anthropic() -> list[dict]:
    if not settings.anthropic_api_key:
        logger.warning("find_daily_viral_topics[anthropic]: ANTHROPIC_API_KEY belum di-set, dilewati")
        return []

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    max_topics = settings.viral_discovery_max_topics

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
        {"name": _TOOL_NAME, "description": _TOOL_DESCRIPTION, "input_schema": _TOOL_PARAMETERS},
    ]

    messages: list[dict] = [
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram/Facebook/TikTok publik Indonesia)."}
    ]
    found_items: list[dict] = []

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=_system_prompt(max_topics, has_web_search=True),
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            logger.warning("find_daily_viral_topics[anthropic]: Claude refusal")
            return []

        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME]
        if not tool_uses:
            break

        tool_results = []
        for block in tool_uses:
            items = block.input.get("items", [])
            found_items.extend(_extract_items(items, max_topics))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Diterima {len(items)} topik.",
            })
        messages.append({"role": "user", "content": tool_results})
        break  # cukup satu putaran submit, tidak perlu lanjut agentic loop

    found_items = found_items[:max_topics]
    logger.info("find_daily_viral_topics[anthropic]: ditemukan %d topik (dengan akun instagram)", len(found_items))
    return found_items


# ─────────────────────────────────────────────────────────────────────────────
# Provider: OpenAI — function calling saja, TIDAK ADA browsing bawaan
# ─────────────────────────────────────────────────────────────────────────────

async def _find_via_openai() -> list[dict]:
    if not settings.openai_api_key:
        logger.warning("find_daily_viral_topics[openai]: OPENAI_API_KEY belum di-set, dilewati")
        return []

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    max_topics = settings.viral_discovery_max_topics

    tools = [{
        "type": "function",
        "function": {"name": _TOOL_NAME, "description": _TOOL_DESCRIPTION, "parameters": _TOOL_PARAMETERS},
    }]
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(max_topics, has_web_search=False)},
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram/Facebook/TikTok publik Indonesia)."},
    ]

    found_items: list[dict] = []
    response = client.chat.completions.create(model=settings.openai_model, messages=messages, tools=tools)
    msg = response.choices[0].message

    for call in msg.tool_calls or []:
        if call.function.name != _TOOL_NAME:
            continue
        args = json.loads(call.function.arguments)
        found_items.extend(_extract_items(args.get("items", []), max_topics))

    logger.info("find_daily_viral_topics[openai]: ditemukan %d topik (TANPA browsing — cek akurasi)", len(found_items))
    return found_items[:max_topics]


# ─────────────────────────────────────────────────────────────────────────────
# Provider: Ollama (lokal) — endpoint kompatibel OpenAI, browsing via
# Firecrawl (primary) dengan auto-switch fallback ke Tavily
# ─────────────────────────────────────────────────────────────────────────────

def _format_search_results(results: list[dict], title_key: str, snippet_key: str, url_key: str) -> str:
    if not results:
        return "Tidak ada hasil untuk pencarian ini."
    lines = [
        f"- {r.get(title_key, '')}: {(r.get(snippet_key) or '')[:300]} (sumber: {r.get(url_key, '')})"
        for r in results
    ]
    return "\n".join(lines)


def _extract_account_candidates(text: str, max_candidates: int = 8) -> list[dict]:
    """
    Ekstraksi kandidat akun Facebook/Instagram LANGSUNG dari URL literal di
    teks hasil pencarian — dijalankan oleh kode ini, bukan ditebak model.
    Kenapa perlu: qwen3:8b terbukti (lihat memory project_ollama_websearch_quality)
    tidak mengenali ID numerik Facebook (mis. "100090312503944") sebagai akun
    valid kalau cuma disuruh baca teks bebas.

    Gampang dimodifikasi: filter "bukan akun" ada di _FB_RESERVED_PATHS /
    _IG_RESERVED_PATHS di atas — tinggal tambah entri baru di situ kalau ada
    pola URL non-akun yang lolos.
    """
    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []

    for platform, pattern, reserved in (
        ("facebook", _FB_URL_RE, _FB_RESERVED_PATHS),
        ("instagram", _IG_URL_RE, _IG_RESERVED_PATHS),
        ("tiktok", _TT_URL_RE, _TT_RESERVED_PATHS),
    ):
        for match in pattern.finditer(text):
            slug = match.group(1)
            if len(slug) < 3 or slug.lower() in reserved:
                continue
            key = (platform, slug)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"platform": platform, "username": slug})
            if len(candidates) >= max_candidates:
                return candidates

    return candidates


def _append_account_candidates(search_text: str) -> str:
    """Tempel daftar kandidat akun (hasil _extract_account_candidates) ke
    akhir teks hasil pencarian, supaya model punya pilihan eksplisit alih-alih
    menebak dari teks bebas. Tidak mengubah apa pun kalau tidak ada kandidat."""
    candidates = _extract_account_candidates(search_text)
    if not candidates:
        return search_text

    candidate_lines = "\n".join(
        f"  - platform={c['platform']} username={c['username']}" for c in candidates
    )
    return (
        f"{search_text}\n\n"
        "[KANDIDAT AKUN TERDETEKSI OTOMATIS DARI URL DI ATAS — kalau salah satu "
        "relevan dengan topik yang kamu temukan, WAJIB pakai username PERSIS "
        "seperti ini (termasuk kalau berupa angka). JANGAN membuat username "
        "baru selain yang ada di daftar ini selama daftar ini tidak kosong]\n"
        f"{candidate_lines}"
    )


async def _firecrawl_search(query: str, max_results: int = 5) -> str:
    """
    Eksekusi pencarian NYATA ke Firecrawl — dipanggil oleh kode ini sendiri
    (BUKAN oleh Ollama, model tidak punya akses jaringan). Raise httpx.HTTPError
    kalau gagal supaya _web_search() bisa fallback ke Tavily.
    """
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.firecrawl.dev/v1/search",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
            json={"query": query, "limit": max_results},
        )
        resp.raise_for_status()
        data = resp.json()

    return _format_search_results(data.get("data", []), "title", "description", "url")


async def _tavily_search(query: str, max_results: int = 5) -> str:
    """
    Eksekusi pencarian NYATA ke Tavily — fallback kalau Firecrawl gagal/tidak
    dikonfigurasi. Raise httpx.HTTPError kalau gagal (ditangkap _web_search()).
    """
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return _format_search_results(data.get("results", []), "title", "content", "url")


async def _web_search(query: str, max_results: int = 5) -> str:
    """
    Dispatcher auto-switch: coba Firecrawl dulu (hasil lebih relevan/spesifik
    per perbandingan langsung), fallback ke Tavily kalau Firecrawl gagal/limit/
    key kosong. Kalau keduanya gagal/kosong, kasih tahu model apa adanya
    (BUKAN pura-pura ada hasil) supaya model tidak mengarang seolah-olah punya
    data pencarian nyata.

    Hasil akhir (kalau ada) DITEMPEL kandidat akun via _append_account_candidates()
    sebelum dikirim ke model — lihat komentar di fungsi itu untuk alasannya.
    """
    import httpx

    if settings.firecrawl_api_key:
        try:
            text = await _firecrawl_search(query, max_results)
            return _append_account_candidates(text)
        except httpx.HTTPError as exc:
            logger.warning("_web_search: Firecrawl gagal (%s), fallback ke Tavily", exc)

    if settings.tavily_api_key:
        try:
            text = await _tavily_search(query, max_results)
            return _append_account_candidates(text)
        except httpx.HTTPError as exc:
            logger.warning("_web_search: Tavily juga gagal: %s", exc)
            return f"Pencarian gagal (Firecrawl & Tavily error): {exc}"

    return "Web search tidak tersedia (FIRECRAWL_API_KEY/TAVILY_API_KEY belum di-set)."


async def _find_via_ollama() -> list[dict]:
    from openai import OpenAI

    base_url = f"{settings.ollama_base_url}/v1"
    client = OpenAI(base_url=base_url, api_key="ollama")  # api_key diabaikan Ollama, tapi wajib diisi
    max_topics = settings.viral_discovery_max_topics
    has_web_search = bool(settings.firecrawl_api_key or settings.tavily_api_key)

    if not has_web_search:
        logger.warning(
            "find_daily_viral_topics[ollama]: FIRECRAWL_API_KEY/TAVILY_API_KEY belum di-set, jalan TANPA browsing"
        )

    tools = [{
        "type": "function",
        "function": {"name": _TOOL_NAME, "description": _TOOL_DESCRIPTION, "parameters": _TOOL_PARAMETERS},
    }]
    if has_web_search:
        tools.insert(0, {
            "type": "function",
            "function": {
                "name": _WEB_SEARCH_TOOL_NAME,
                "description": _WEB_SEARCH_TOOL_DESCRIPTION,
                "parameters": _WEB_SEARCH_TOOL_PARAMETERS,
            },
        })

    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(max_topics, has_web_search=has_web_search)},
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram/Facebook/TikTok publik Indonesia)."},
    ]

    found_items: list[dict] = []
    forced_retries_used = 0  # lihat FORCE_RETRY_LIMIT di atas

    for _ in range(MAX_ITERATIONS):
        response = client.chat.completions.create(model=settings.ollama_model_name, messages=messages, tools=tools)
        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []

        assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            # Model menyerah (jawab teks biasa) tanpa pernah submit apa pun.
            # Paksa coba lagi maksimal FORCE_RETRY_LIMIT kali sebelum benar-benar
            # berhenti — qwen3:8b sering BILANG mau cari lagi tapi tidak
            # benar-benar melakukannya (lihat memory project_ollama_websearch_quality).
            if not found_items and has_web_search and forced_retries_used < FORCE_RETRY_LIMIT:
                forced_retries_used += 1
                messages.append({
                    "role": "user",
                    "content": (
                        "Kamu belum memanggil tool apa pun dan belum submit topik. "
                        "WAJIB coba web_search LAGI dengan query LEBIH SPESIFIK — "
                        "sertakan nama topik yang sudah kamu temukan + kata "
                        '"facebook"/"instagram" — sebelum benar-benar menyerah. Kalau '
                        "hasil kali ini masih tidak ada akun, baru boleh berhenti."
                    ),
                })
                continue
            break

        submit_calls = [c for c in tool_calls if c.function.name == _TOOL_NAME]
        search_calls = [c for c in tool_calls if c.function.name == _WEB_SEARCH_TOOL_NAME]

        for call in submit_calls:
            args = json.loads(call.function.arguments)
            items = args.get("items", [])
            found_items.extend(_extract_items(items, max_topics))
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": f"Diterima {len(items)} topik.",
            })

        for call in search_calls:
            args = json.loads(call.function.arguments)
            result_text = await _web_search(args.get("query", ""))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result_text})

        if submit_calls:
            break  # cukup satu putaran submit, tidak perlu lanjut agentic loop
        if not search_calls:
            break  # tool_call tidak dikenali sama sekali, hindari infinite loop

    found_items = found_items[:max_topics]
    logger.info(
        "find_daily_viral_topics[ollama]: ditemukan %d topik (web_search=%s)",
        len(found_items), has_web_search,
    )
    return found_items
