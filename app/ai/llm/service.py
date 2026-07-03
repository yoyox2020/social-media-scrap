"""
AI keyword research — dipanggil saat GET /instagram/posts/search tidak menemukan
apapun di DB. Claude (via web_search) mencari topik + akun Instagram nyata terkait
keyword yang dicari, hasilnya di-submit ke trend_recommendations (status=pending)
supaya diambil pipeline scrape Instagram yang SUDAH ADA (budget harian, jadwal
09:00 WIB) — lihat docs/trend-recommendations.md dan docs/setting-tool-calling.md.

Bukan HTTP call ke POST /trend-recommendations sendiri — dipanggil langsung
sebagai fungsi Python (submit_recommendations) karena sudah dalam proses yang sama.
"""
from __future__ import annotations

import logging

from app.shared.config import settings

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_trend_topics"

_TOOL_DESCRIPTION = (
    "Submit topik/akun Instagram yang BENAR-BENAR ditemukan lewat web search terkait "
    "keyword yang diberikan. Panggil ini SETELAH menemukan data nyata — jangan mengarang "
    "topik atau username yang tidak terverifikasi."
)

_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "Daftar topik terkait keyword, tiap topik object dengan topic/score/related_accounts",
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

_SYSTEM_PROMPT = (
    "Kamu adalah AI trend-analyst. User mencari post Instagram dengan keyword tertentu "
    "tapi tidak ketemu di database. Tugasmu: gunakan web_search untuk menemukan topik/isu "
    "NYATA terkait keyword itu di Indonesia, WAJIB sertakan akun Instagram resmi/relevan "
    "yang mendorong viralitasnya (platform='instagram'). Kalau tidak menemukan akun "
    "Instagram sama sekali untuk suatu topik, jangan sertakan topik itu. Maksimal 5 topik. "
    "Setelah menemukan data nyata, panggil tool submit_trend_topics."
)

MAX_ITERATIONS = 8


async def find_instagram_topics_for_keyword(keyword: str) -> list[dict]:
    """
    Cari topik + akun Instagram nyata terkait `keyword` via Claude (web_search +
    tool use). Return list item siap dipakai `TrendRecommendationItem`
    (topic/score/related_accounts) — HANYA yang punya minimal 1 akun Instagram.
    Return list kosong kalau Claude tidak menemukan apapun atau API key belum di-set.
    """
    if not settings.anthropic_api_key:
        logger.warning("find_instagram_topics_for_keyword: ANTHROPIC_API_KEY belum di-set, dilewati")
        return []

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
        {"name": _TOOL_NAME, "description": _TOOL_DESCRIPTION, "input_schema": _TOOL_PARAMETERS},
    ]

    messages: list[dict] = [
        {"role": "user", "content": f"Cari topik trending terkait keyword: {keyword}"}
    ]
    found_items: list[dict] = []

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            logger.warning("find_instagram_topics_for_keyword: Claude refusal untuk keyword=%s", keyword)
            return []

        if response.stop_reason == "pause_turn":
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME]
        if not tool_uses:
            break

        tool_results = []
        for block in tool_uses:
            items = block.input.get("items", [])
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

    logger.info(
        "find_instagram_topics_for_keyword: keyword=%s ditemukan %d topik (dengan akun instagram)",
        keyword, len(found_items),
    )
    return found_items
