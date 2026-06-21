"""
SummaryAgent — menggunakan Qwen3 8B (via Ollama) untuk membuat ringkasan akhir
dari semua hasil agent yang sudah berjalan.

Jika Ollama tidak tersedia, fallback ke template-based summary.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agents.base import BaseAgent
from app.services.agents.schemas import AgentContext, AgentResult


class SummaryAgent(BaseAgent):
    name = "summary"
    description = "Membuat jawaban akhir menggunakan Qwen3 8B berdasarkan hasil semua agent"

    def __init__(self, db: AsyncSession, use_llm: bool = True):
        super().__init__(db)
        self.use_llm = use_llm

    async def run(
        self,
        context: AgentContext,
        agent_results: dict[str, AgentResult] | None = None,
    ) -> AgentResult:
        try:
            results = agent_results or {}
            summaries = {
                name: r.summary
                for name, r in results.items()
                if r.summary and not r.error
            }

            if not summaries:
                return self._ok(
                    data={},
                    summary="Tidak ada data yang cukup untuk membuat ringkasan.",
                )

            if self.use_llm:
                try:
                    answer = await self._summarize_with_llm(context.question, summaries)
                except Exception:
                    answer = self._summarize_with_template(context.question, summaries)
            else:
                answer = self._summarize_with_template(context.question, summaries)

            return self._ok(
                data={"agent_summaries": summaries},
                summary=answer,
            )
        except Exception as exc:
            return self._err(str(exc))

    async def _summarize_with_llm(self, question: str, summaries: dict[str, str]) -> str:
        from app.services.ai.llm_client import OllamaClient

        context_text = "\n".join(
            f"- [{agent.upper()}]: {summary}"
            for agent, summary in summaries.items()
        )

        prompt = f"""Pertanyaan user: "{question}"

Data analisis dari berbagai agent:
{context_text}

Berikan jawaban yang komprehensif, ringkas, dan langsung menjawab pertanyaan user
berdasarkan data di atas. Gunakan Bahasa Indonesia. Maksimal 200 kata."""

        system = (
            "Kamu adalah analis media sosial yang memberikan insight berdasarkan data nyata. "
            "Jawab secara faktual dan objektif."
        )

        client = OllamaClient()
        return await client.generate(prompt, system_prompt=system, temperature=0.3, max_tokens=300)

    def _summarize_with_template(self, question: str, summaries: dict[str, str]) -> str:
        parts = [f"Analisis untuk: '{question}'\n"]
        for agent, summary in summaries.items():
            parts.append(f"• {summary}")
        return "\n".join(parts)
