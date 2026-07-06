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
- "ollama": browsing lewat tool `web_search` CUSTOM (Tavily API, butuh
  TAVILY_API_KEY di .env) — model TIDAK bisa akses internet sendiri, jadi
  kode di sini yang benar-benar eksekusi pencarian lalu kirim hasilnya balik
  ke model (pola function-calling standar). Kalau TAVILY_API_KEY kosong,
  fallback ke pengetahuan training model (bisa basi/salah, skor rendah).
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


_SUPPORTED_PLATFORMS = {"instagram", "facebook"}


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
        "WAJIB sertakan minimal SATU akun nyata — Instagram (platform='instagram') "
        "DAN/ATAU Facebook (platform='facebook') — yang mendorong viralitasnya untuk "
        "tiap topik. Akun itu HARUS benar-benar disebutkan namanya di hasil web_search "
        "(judul/isi/URL) — JANGAN PERNAH mengarang atau menebak username yang tidak "
        "muncul eksplisit di hasil pencarian. Kalau hasil pencarian cuma bahas topik "
        "tanpa menyebut akun spesifik, cari lagi dengan query lain (query berbeda) "
        "sebelum menyerah pada topik itu."
        if has_web_search
        else
        "WAJIB sertakan minimal SATU akun nyata — Instagram (platform='instagram') "
        "DAN/ATAU Facebook (platform='facebook') — yang mendorong viralitasnya untuk "
        "tiap topik."
    )
    return (
        f"Kamu adalah AI trend-analyst. Hari ini tanggal {today}. Tugasmu HARI INI: "
        f"{browsing_note} topik/isu yang BENAR-BENAR sedang viral di berita Indonesia "
        "dan media sosial publik (Instagram dan/atau Facebook) — bukan satu keyword "
        "tertentu, tapi sapuan terbuka (bisa politik, hiburan, olahraga, produk "
        f"viral, dll). {account_rule} Kalau tidak menemukan akun Instagram MAUPUN "
        f"Facebook sama sekali untuk suatu topik, jangan sertakan topik itu. Maksimal "
        f"{max_topics} topik, jangan mengarang untuk mencapai jumlah itu — kalau cuma "
        "menemukan lebih sedikit, submit yang nyata saja. Setelah menemukan data "
        f"nyata, panggil tool {_TOOL_NAME}."
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
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram publik Indonesia)."}
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
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram publik Indonesia)."},
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
# Provider: Ollama (lokal) — endpoint kompatibel OpenAI, browsing via Tavily
# ─────────────────────────────────────────────────────────────────────────────

async def _tavily_search(query: str, max_results: int = 5) -> str:
    """
    Eksekusi pencarian NYATA ke Tavily — dipanggil oleh kode ini sendiri
    (BUKAN oleh Ollama, model tidak punya akses jaringan), sebagai respons
    atas tool_call `web_search` yang diminta model. Hasilnya dikirim balik
    ke model sebagai pesan role="tool".
    """
    import httpx

    try:
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
    except httpx.HTTPError as exc:
        logger.warning("_tavily_search error untuk query=%r: %s", query, exc)
        return f"Pencarian gagal: {exc}"

    results = data.get("results", [])
    if not results:
        return "Tidak ada hasil untuk pencarian ini."

    lines = [
        f"- {r.get('title', '')}: {(r.get('content') or '')[:300]} (sumber: {r.get('url', '')})"
        for r in results
    ]
    return "\n".join(lines)


async def _find_via_ollama() -> list[dict]:
    from openai import OpenAI

    base_url = f"{settings.ollama_base_url}/v1"
    client = OpenAI(base_url=base_url, api_key="ollama")  # api_key diabaikan Ollama, tapi wajib diisi
    max_topics = settings.viral_discovery_max_topics
    has_web_search = bool(settings.tavily_api_key)

    if not has_web_search:
        logger.warning("find_daily_viral_topics[ollama]: TAVILY_API_KEY belum di-set, jalan TANPA browsing")

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
        {"role": "user", "content": "Cari topik/akun yang sedang viral hari ini (berita + Instagram/Facebook publik Indonesia)."},
    ]

    found_items: list[dict] = []

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
            result_text = await _tavily_search(args.get("query", ""))
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
