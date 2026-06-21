"""
PlannerAgent — menentukan agent mana yang perlu dipanggil berdasarkan pertanyaan user.

Dua mode:
1. Rule-based  (use_llm=False, default) — cepat, tidak perlu Qwen3
2. LLM-based   (use_llm=True)  — akurat, butuh Ollama/Qwen3 aktif

Output: list nama agent yang akan dijalankan secara berurutan, selalu diakhiri "summary".
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Kata kunci → agent mapping untuk rule-based planning
_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["cari", "temukan", "find", "search", "topik tentang", "bahas"], "search"),
    (["sentimen", "perasaan", "opini", "positif", "negatif", "neutral", "pendapat"], "sentiment"),
    (["siapa", "tokoh", "orang", "person", "organisasi", "perusahaan", "brand", "entity"], "entity"),
    (["tren", "trend", "waktu", "periode", "minggu", "bulan", "naik", "turun", "volume"], "trend"),
]

_DEFAULT_PLAN = ["sentiment", "entity", "trend", "summary"]
_AVAILABLE_AGENTS = ["search", "sentiment", "entity", "trend", "summary"]


class PlannerAgent:
    def __init__(self, db: "AsyncSession"):
        self.db = db

    async def plan(self, question: str, use_llm: bool = False) -> list[str]:
        """
        Buat execution plan berdasarkan pertanyaan.

        Returns:
            list agent names diurutkan (selalu diakhiri 'summary')
        """
        if use_llm:
            try:
                return await self._plan_with_llm(question)
            except Exception:
                pass  # fallback ke rule-based

        return self._plan_with_rules(question)

    def _plan_with_rules(self, question: str) -> list[str]:
        question_lower = question.lower()
        selected: set[str] = set()

        for keywords, agent in _KEYWORD_RULES:
            if any(kw in question_lower for kw in keywords):
                selected.add(agent)

        if not selected:
            selected = set(_DEFAULT_PLAN) - {"summary"}

        # summary selalu ada di akhir
        plan = sorted(selected, key=lambda a: _AVAILABLE_AGENTS.index(a))
        plan.append("summary")
        return plan

    async def _plan_with_llm(self, question: str) -> list[str]:
        from app.services.ai.llm_client import OllamaClient

        client = OllamaClient()
        prompt = f"""Kamu adalah orchestrator multi-agent sistem analitik media sosial.

Pertanyaan user: "{question}"

Agent yang tersedia:
- search: mencari post yang relevan dengan topik
- sentiment: menganalisis distribusi sentimen (positif/negatif/neutral)
- entity: mengextract dan menganalisis entitas (orang, organisasi, lokasi)
- trend: menganalisis tren volume dan sentimen over time
- summary: membuat ringkasan akhir (SELALU dipanggil terakhir)

Tentukan agent mana yang perlu dipanggil berdasarkan pertanyaan.
Jawab HANYA dengan JSON array seperti ini: ["search", "sentiment", "summary"]
Jangan ada teks lain selain JSON array."""

        raw = await client.generate(prompt, temperature=0.1, max_tokens=50)

        # Extract JSON array dari response
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not match:
            return self._plan_with_rules(question)

        parsed = json.loads(match.group())
        agents = [a for a in parsed if a in _AVAILABLE_AGENTS]

        if "summary" not in agents:
            agents.append("summary")

        return agents if agents else _plan_with_rules(question)
