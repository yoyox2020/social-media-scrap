"""
Ollama LLM Client — async HTTP client ke Ollama API untuk Qwen3 8B.

Digunakan untuk reasoning, summarization, dan Q&A berbasis konten.
Endpoint utama: http://ollama:11434 (di Docker network)

Dokumentasi Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
"""
from __future__ import annotations

import httpx

from app.shared.exceptions import ExternalAPIError


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 120,
    ):
        from app.shared.config import settings

        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model_name
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> str:
        """
        Generate text menggunakan Ollama /api/chat endpoint.

        Args:
            prompt:        User message
            system_prompt: Optional system instruction
            temperature:   0.0 (deterministik) – 1.0 (kreatif)
            max_tokens:    Maximum output tokens

        Returns:
            Generated text string
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["message"]["content"].strip()
        except httpx.TimeoutException as exc:
            raise ExternalAPIError(f"Ollama timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ExternalAPIError(f"Ollama HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except Exception as exc:
            raise ExternalAPIError(f"Ollama error: {exc}") from exc

    async def summarize(self, text: str, max_words: int = 50, lang: str = "id") -> str:
        """Ringkas konten post dalam bahasa yang sesuai."""
        if lang == "id":
            prompt = (
                f"Ringkas teks berikut dalam {max_words} kata atau kurang "
                f"menggunakan Bahasa Indonesia:\n\n{text[:1500]}\n\nRingkasan:"
            )
        else:
            prompt = (
                f"Summarize the following text in {max_words} words or fewer:\n\n"
                f"{text[:1500]}\n\nSummary:"
            )
        system = (
            "Kamu adalah asisten yang membantu meringkas konten media sosial. "
            "Berikan ringkasan yang singkat, objektif, dan informatif."
        )
        return await self.generate(prompt, system_prompt=system, temperature=0.2)

    async def classify_topic(self, text: str, topics: list[str]) -> str:
        """Klasifikasi teks ke dalam salah satu topik yang diberikan."""
        topics_str = ", ".join(topics)
        prompt = (
            f"Klasifikasikan teks berikut ke dalam salah satu topik: {topics_str}\n\n"
            f"Teks: {text[:500]}\n\n"
            f"Jawab hanya dengan nama topik tanpa penjelasan:"
        )
        result = await self.generate(prompt, temperature=0.1, max_tokens=20)
        # Pastikan hasilnya adalah salah satu dari topik yang diberikan
        result_clean = result.strip().strip(".")
        for topic in topics:
            if topic.lower() in result_clean.lower():
                return topic
        return topics[0]  # fallback ke topik pertama

    async def health_check(self) -> bool:
        """Cek apakah Ollama service bisa diakses dan model tersedia."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code != 200:
                    return False
                tags = response.json().get("models", [])
                return any(self.model in t.get("name", "") for t in tags)
        except Exception:
            return False
