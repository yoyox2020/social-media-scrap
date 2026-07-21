"""
Client validasi LLM via OpenRouter -- reuse SDK `openai` yg SUDAH jadi
dependency (pyproject.toml), krn OpenRouter API-compatible dgn OpenAI Chat
Completions (cuma beda base_url). TIDAK pakai Ollama (beda dari
app/services/agents/ yg semua LLM-nya Ollama) -- permintaan eksplisit user
2026-07-18 pakai OpenRouter dgn key/model sendiri.
"""
from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI, RateLimitError

from app.services.youtube_discovery.config import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)


class DiscoveryRateLimitError(Exception):
    """Rate limit (HTTP 429) dari key/model yg dipakai -- kegagalan SEMENTARA,
    BUKAN indikasi kandidat jelek. Di-raise TERPISAH dari (False, reason) biasa
    supaya pemanggil (agent.py) bisa coba key/model CADANGAN sebelum benar2
    menyerah, drpd langsung reject konservatif."""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """LLM kadang bungkus jawaban JSON dgn ```json fences atau teks
    pengantar -- coba beberapa cara ekstrak sebelum menyerah."""
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
    # fallback: cari substring {...} pertama
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


async def validate_candidate(
    api_key: str,
    model: str,
    *,
    title: str,
    description: str,
    channel: str,
    published_at: str | None,
    views: int,
    likes: int,
    comments: int,
    topic: str | None,
) -> tuple[bool, str]:
    """
    Minta LLM menilai SATU kandidat video: genuinely baru/trending DAN
    datanya masuk akal (BUKAN cek existensi duplikat -- itu sudah
    ditangani terpisah via external_id sebelum sampai ke sini). Return
    (valid, reason). GAGAL PARSING/ERROR API -> dianggap TIDAK valid
    (conservative -- lebih aman skip drpd salah simpan data jelek).
    """
    topic_line = f'Topik yang dicari: "{topic}"\n' if topic else "Mode: pencarian bebas (tidak terikat topik spesifik).\n"
    prompt = (
        "Kamu menilai APAKAH sebuah video YouTube layak dianggap 'baru viral/trending' "
        "dan datanya konsisten, SEBELUM disimpan ke database.\n\n"
        f"{topic_line}"
        f"Judul: {title}\n"
        f"Deskripsi: {(description or '')[:500]}\n"
        f"Channel: {channel}\n"
        f"Tanggal upload: {published_at or 'tidak diketahui'}\n"
        f"Views: {views}, Likes: {likes}, Comments: {comments}\n\n"
        "Kriteria TOLAK (valid=false):\n"
        "- Judul/deskripsi terindikasi konten LAMA yang cuma kebetulan baru terdeteksi "
        "(bukan genuinely video baru), mis. reupload, kompilasi lama, dsb\n"
        "- Data metrik terlihat tidak masuk akal (mis. views=0 tapi diklaim viral)\n"
        "- Kalau ada topik: video TIDAK relevan sama sekali dgn topik tsb\n\n"
        'Jawab HANYA JSON valid, format persis: {"valid": true/false, "reason": "alasan singkat 1 kalimat"}'
    )

    # timeout=45s (bukan 30s) -- model REASONING spt nemotron butuh waktu
    # generate lebih lama (300-400+ token reasoning SEBELUM jawaban), 30s
    # kadang mepet utk prompt yg lebih panjang -- dinaikkan 2026-07-18
    # bareng max_tokens setelah ditemukan live: 39% panggilan validate_candidate
    # gagal parse (respons kepotong) di produksi dgn max_tokens=600. Default
    # SDK (600s) TETAP terlalu lama utk loop serial ratusan kandidat (184
    # kandidat, 1 request nge-hang bisa nyeret seluruh run bermenit-menit) --
    # reject cepat lebih baik drpd nunggu lama tanpa hasil (fail-safe
    # conservative-reject di bawah, "gagal cepat" bukan "gagal lambat").
    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=45.0)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            # 1500 (bukan 600) -- 600 TERBUKTI tidak selalu cukup utk model
            # REASONING spt nemotron (fallback): reasoning tokens bervariasi
            # tergantung kompleksitas judul/deskripsi kandidat, ditemukan live
            # 2026-07-18 (39% gagal parse krn respons kepotong di tengah
            # reasoning/JSON walau SATU tes manual dgn prompt realistis
            # sempat berhasil di 600 token -- variasinya per-kandidat, jadi
            # perlu headroom jauh lebih besar drpd sekadar nge-pas).
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content or ""
    except RateLimitError as exc:
        raise DiscoveryRateLimitError(str(exc)[:300]) from exc
    except Exception as exc:
        logger.warning("validate_candidate: panggilan OpenRouter gagal (%s), kandidat di-skip (conservative)", exc)
        return False, f"LLM call error: {exc}"

    parsed = _extract_json(raw)
    if not parsed or "valid" not in parsed:
        logger.warning("validate_candidate: gagal parse respons LLM: %r", raw[:300])
        return False, "Gagal parse respons LLM"

    return bool(parsed["valid"]), str(parsed.get("reason", ""))[:500]
