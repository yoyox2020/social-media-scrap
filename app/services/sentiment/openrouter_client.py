"""Client klasifikasi sentiment via LLM (OpenRouter) -- opini KEDUA utk
kasus yg lexicon (rule-based, khusus Bahasa Indonesia) kemungkinan besar
salah: komentar BUKAN Bahasa Indonesia, atau lexicon bilang "netral"
(kasus paling sering salah krn slang tidak dikenali kamus). Di-port dari
`main` branch (PERNAH live-tested), TIDAK diubah -- cuma path pindah."""
from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI, RateLimitError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_VALID_LABELS = {"positif", "negatif", "netral"}


class SentimentRateLimitError(Exception):
    """Rate limit dari OpenRouter (HTTP 429) -- kegagalan SEMENTARA (limit
    harian free-tier / provider congested), BUKAN masalah pada komentarnya.
    Di-raise TERPISAH dari return None (kegagalan lain) supaya pemanggil
    bisa membedakan: rate-limit -> JANGAN tandai komentar sbg sudah dicek
    (biar di-retry run berikutnya setelah limit reset), kegagalan lain ->
    tandai sudah dicek spt biasa (hindari retry selamanya)."""


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


async def classify_sentiment(
    api_key: str,
    model: str,
    *,
    content: str,
) -> tuple[str, str] | None:
    """Minta LLM menilai sentiment SATU komentar (positif/negatif/netral).
    Return (label, reason) atau None kalau panggilan/parsing gagal (BUKAN
    exception -- pemanggil tetap lanjut, baris komentar itu cuma dilewati
    utk batch ini, tidak menggagalkan komentar lain)."""
    prompt = (
        "Kamu menilai SENTIMEN satu komentar dari media sosial. "
        "Komentar ini bisa dalam bahasa APAPUN (Indonesia, Inggris, atau bahasa lain) -- "
        "nilai berdasarkan ISI/maksudnya, bukan dari kata kunci per kata.\n\n"
        f"Komentar: \"{(content or '')[:1000]}\"\n\n"
        "Klasifikasikan sebagai salah satu: \"positif\", \"negatif\", atau \"netral\".\n"
        "- positif: memuji, setuju, senang, mendukung\n"
        "- negatif: mengkritik, marah, kecewa, menuduh, mengejek\n"
        "- netral: sekadar informasi/pertanyaan/tidak menyatakan opini jelas\n\n"
        'Jawab HANYA JSON valid, format persis: {"label": "positif/negatif/netral", "reason": "alasan singkat 1 kalimat"}'
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=30.0)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500,
        )
        raw = resp.choices[0].message.content or ""
    except RateLimitError as exc:
        raise SentimentRateLimitError(str(exc)[:300]) from exc
    except Exception as exc:
        logger.warning("classify_sentiment: panggilan OpenRouter gagal (%s)", exc)
        return None

    parsed = _extract_json(raw)
    if not parsed or "label" not in parsed:
        logger.warning("classify_sentiment: gagal parse respons LLM: %r", raw[:300])
        return None

    label = str(parsed["label"]).strip().lower()
    if label not in _VALID_LABELS:
        logger.warning("classify_sentiment: label di luar dugaan: %r", label)
        return None

    return label, str(parsed.get("reason", ""))[:500]
