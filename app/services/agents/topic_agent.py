"""
TopicAgent — mendeteksi dan mengklasifikasikan topik dari kumpulan post menggunakan Qwen3.

Pipeline:
  1. Ambil sample post yang belum diklasifikasikan
  2. Gunakan Qwen3 untuk ekstrak topik dominan dari batch post
  3. Simpan ke tabel topics
"""
from __future__ import annotations

import json
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult

_BATCH_SIZE = 20  # jumlah post per request ke Qwen3


class TopicAgent(BaseAgent):
    name = "topic"
    description = "Mendeteksi topik-topik yang dibahas dalam kumpulan post menggunakan Qwen3"

    def __init__(self, db: AsyncSession, num_topics: int = 5):
        super().__init__(db)
        self.num_topics = num_topics

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            posts = await self._fetch_sample_posts(context)
            if not posts:
                return self._ok(
                    data={"topics": []},
                    summary="Tidak ada post yang bisa dianalisis topiknya.",
                )

            topics = await self._detect_topics(posts, context.keyword_text)

            summary = (
                f"Terdeteksi {len(topics)} topik dari {len(posts)} post sample: "
                + ", ".join(t["name"] for t in topics[:3])
                + ("..." if len(topics) > 3 else ".")
            )

            return self._ok(
                data={"topics": topics, "posts_analyzed": len(posts)},
                summary=summary,
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _fetch_sample_posts(self, context: AgentContext) -> list[str]:
        stmt = (
            select(Post.cleaned_content)
            .where(
                Post.keyword_id == context.keyword_id,
                Post.cleaned_content.is_not(None),
                func.length(Post.cleaned_content) > 30,
            )
            .order_by(func.random())
            .limit(_BATCH_SIZE)
        )
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all() if row[0]]

    async def _detect_topics(self, posts: list[str], keyword: str) -> list[dict]:
        from app.services.ai.llm_client import OllamaClient

        sample = "\n".join(f"- {p[:200]}" for p in posts[:15])
        prompt = f"""Dari kumpulan post media sosial tentang "{keyword}" berikut:

{sample}

Identifikasi {self.num_topics} topik utama yang paling banyak dibahas.
Jawab HANYA dengan JSON array seperti ini:
[
  {{"name": "Nama Topik", "description": "Deskripsi singkat", "keywords": ["kata1", "kata2"]}},
  ...
]
Jangan ada teks lain selain JSON array."""

        client = OllamaClient()
        raw = await client.generate(prompt, temperature=0.3, max_tokens=400)

        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not match:
            return []

        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return []
