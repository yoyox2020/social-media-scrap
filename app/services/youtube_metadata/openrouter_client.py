"""
Generate ringkasan "konteks viral" via LLM (OpenRouter) -- KENAPA/apa
konteks video ini viral/trending, berdasar pengetahuan model + title/
description/tags video SAJA (TANPA pencarian web real-time -- itu SELALU
berbayar di OpenRouter, model gratis tidak include, keputusan user
2026-07-18). Beda dari validate_candidate() di youtube_discovery (yg minta
JSON terstruktur), ini cuma minta teks ringkas biasa.
"""
from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.services.youtube_metadata.config import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 800


async def generate_viral_context(
    api_key: str,
    model: str,
    *,
    title: str,
    description: str,
    tags: list[str],
    views: int,
    likes: int,
    comments: int,
) -> str | None:
    """Return ringkasan singkat (2-4 kalimat) soal konteks/kenapa video ini
    viral, atau None kalau panggilan gagal (BUKAN exception -- pemanggil
    tetap simpan sisa metadata video+channel walau ini gagal)."""
    tags_line = f"Tags: {', '.join(tags[:15])}\n" if tags else ""
    prompt = (
        "Kamu menjelaskan KONTEKS kenapa sebuah video YouTube ini viral/trending, "
        "berdasarkan judul, deskripsi, dan statistiknya.\n\n"
        f"Judul: {title}\n"
        f"Deskripsi: {(description or '')[:600]}\n"
        f"{tags_line}"
        f"Views: {views:,}, Likes: {likes:,}, Comments: {comments:,}\n\n"
        "Tulis 2-4 kalimat singkat (Bahasa Indonesia) menjelaskan KONTEKS/kenapa "
        "topik atau video ini kemungkinan sedang ramai dibicarakan -- fokus ke "
        "substansi topiknya, BUKAN cuma mengulang angka statistik di atas. "
        "Kalau topiknya tidak kamu kenali/kurang jelas dari info yang ada, katakan "
        "itu terus terang (jangan mengarang). Jawab HANYA teks penjelasannya, "
        "tanpa judul/label tambahan."
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=30.0)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text[:MAX_CONTEXT_CHARS] if text else None
    except Exception as exc:
        logger.warning("generate_viral_context: panggilan OpenRouter gagal (%s)", exc)
        return None
