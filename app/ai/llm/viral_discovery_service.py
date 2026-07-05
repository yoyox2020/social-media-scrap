"""
Viral discovery harian — AI menyapu berita + Instagram publik Indonesia untuk
menemukan topik yang BENAR-BENAR viral HARI INI (bukan satu keyword tertentu,
sapuan terbuka), hasilnya di-submit ke trend_recommendations (status=pending)
supaya diambil pipeline scrape Instagram yang SUDAH ADA (budget harian,
jadwal 09:00 WIB) — lihat docs/trend-recommendations.md.

Provider AI bisa diganti via .env TANPA ubah kode:
    AI_DISCOVERY_PROVIDER=anthropic | openai | ollama

CATATAN PENTING: cuma provider "anthropic" (Claude) yang punya web_search
bawaan — bisa benar-benar cari data hari ini. "openai" dan "ollama" TIDAK
BISA browsing sama sekali (function calling saja), jadi hasilnya cuma dari
pengetahuan training model (bisa basi/salah), BUKAN topik viral hari ini yang
sebenarnya. Dipertahankan supaya provider genuinely bisa diganti, tapi kalau
butuh hasil akurat, pakai "anthropic".

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
    browsing_note = (
        "gunakan web_search untuk menemukan"
        if has_web_search
        else "sebutkan (dari pengetahuanmu — CATATAN: kamu tidak punya akses internet, "
             "jadi ini mungkin bukan data hari ini yang sebenarnya, beri tahu itu di skor rendah)"
    )
    return (
        f"Kamu adalah AI trend-analyst. Tugasmu HARI INI: {browsing_note} "
        "topik/isu yang BENAR-BENAR sedang viral di berita Indonesia dan media "
        "sosial publik (Instagram dan/atau Facebook) — bukan satu keyword "
        "tertentu, tapi sapuan terbuka (bisa politik, hiburan, olahraga, produk "
        "viral, dll). WAJIB sertakan minimal SATU akun nyata — Instagram "
        "(platform='instagram') DAN/ATAU Facebook (platform='facebook') — yang "
        "mendorong viralitasnya untuk tiap topik. Kalau ada keduanya, sertakan "
        "keduanya. Kalau tidak menemukan akun Instagram MAUPUN Facebook sama "
        f"sekali untuk suatu topik, jangan sertakan topik itu. Maksimal {max_topics} "
        "topik, jangan mengarang untuk mencapai jumlah itu — kalau cuma menemukan "
        "lebih sedikit, submit yang nyata saja. Setelah menemukan data nyata, "
        f"panggil tool {_TOOL_NAME}."
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
# Provider: Ollama (lokal) — endpoint kompatibel OpenAI, TIDAK ADA browsing
# ─────────────────────────────────────────────────────────────────────────────

async def _find_via_ollama() -> list[dict]:
    from openai import OpenAI

    base_url = f"{settings.ollama_base_url}/v1"
    client = OpenAI(base_url=base_url, api_key="ollama")  # api_key diabaikan Ollama, tapi wajib diisi
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
    response = client.chat.completions.create(model=settings.ollama_model_name, messages=messages, tools=tools)
    msg = response.choices[0].message

    for call in msg.tool_calls or []:
        if call.function.name != _TOOL_NAME:
            continue
        args = json.loads(call.function.arguments)
        found_items.extend(_extract_items(args.get("items", []), max_topics))

    logger.info("find_daily_viral_topics[ollama]: ditemukan %d topik (TANPA browsing — cek akurasi)", len(found_items))
    return found_items[:max_topics]
