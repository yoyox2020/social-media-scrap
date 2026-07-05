"""
Viral discovery harian — Claude (via web_search) menyapu berita + Instagram
publik Indonesia untuk menemukan topik yang BENAR-BENAR viral HARI INI (bukan
satu keyword tertentu, sapuan terbuka), hasilnya di-submit ke
trend_recommendations (status=pending) supaya diambil pipeline scrape
Instagram yang SUDAH ADA (budget harian, jadwal 09:00 WIB) — lihat
docs/trend-recommendations.md.

Dipanggil oleh app/services/trend_recommendations/viral_discovery_scrape_service.py
(bukan file trend_scrape_service.py yang dibekukan) sebagai bagian dari task
Celery harian workers.viral_discovery.daily_scan.

Bukan HTTP call ke POST /trend-recommendations sendiri — hasilnya dikirim ke
submit_recommendations() langsung sebagai fungsi Python karena sudah dalam
proses yang sama.
"""
from __future__ import annotations

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


def _system_prompt(max_topics: int) -> str:
    return (
        "Kamu adalah AI trend-analyst. Tugasmu HARI INI: gunakan web_search untuk "
        "menemukan topik/isu yang BENAR-BENAR sedang viral di berita Indonesia dan "
        "Instagram publik — bukan satu keyword tertentu, tapi sapuan terbuka (bisa "
        "politik, hiburan, olahraga, produk viral, dll). WAJIB sertakan akun "
        "Instagram nyata (platform='instagram') yang mendorong viralitasnya untuk "
        "tiap topik — kalau tidak menemukan akun Instagram sama sekali untuk suatu "
        f"topik, jangan sertakan topik itu. Maksimal {max_topics} topik, jangan "
        "mengarang untuk mencapai jumlah itu — kalau cuma menemukan lebih sedikit, "
        "submit yang nyata saja. Setelah menemukan data nyata, panggil tool "
        f"{_TOOL_NAME}."
    )


async def find_daily_viral_topics() -> list[dict]:
    """
    Sapuan terbuka harian via Claude (web_search + tool use) untuk topik+akun
    Instagram yang benar-benar viral hari ini. Return list item siap dipakai
    `TrendRecommendationItem` (topic/score/related_accounts) — HANYA yang
    punya minimal 1 akun Instagram. Return list kosong kalau Claude tidak
    menemukan apapun atau API key belum di-set.
    """
    if not settings.anthropic_api_key:
        logger.warning("find_daily_viral_topics: ANTHROPIC_API_KEY belum di-set, dilewati")
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
            system=_system_prompt(max_topics),
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            logger.warning("find_daily_viral_topics: Claude refusal")
            return []

        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME]
        if not tool_uses:
            break

        tool_results = []
        for block in tool_uses:
            items = block.input.get("items", [])[:max_topics]
            for item in items:
                accounts = [a for a in item.get("related_accounts", []) if a.get("platform") == "instagram"]
                if accounts:
                    found_items.append({**item, "related_accounts": accounts})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Diterima {len(items)} topik.",
            })
        messages.append({"role": "user", "content": tool_results})
        break  # cukup satu putaran submit, tidak perlu lanjut agentic loop

    found_items = found_items[:max_topics]
    logger.info("find_daily_viral_topics: ditemukan %d topik viral (dengan akun instagram)", len(found_items))
    return found_items
