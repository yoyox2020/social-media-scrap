"""
Sentiment Agent -- opini KEDUA (LLM) utk komentar YouTube yg lexicon
(rule-based, khusus Bahasa Indonesia, app/ai/lexicon/service.py) kemungkinan
besar SALAH menilai:
  1. Komentar BUKAN Bahasa Indonesia -- lexicon Indonesia otomatis skor 0
     (netral) krn tidak ada kata yg cocok kamus, TERLEPAS dari sentimen
     aslinya (dibuktikan 2026-07-18: 67.5% komentar YouTube berlabel
     "netral", sample manual banyak berisi komentar Inggris/Spanyol/Arab
     yg jelas beropini).
  2. Komentar Indonesia yg lexicon bilang "netral" -- kasus paling sering
     salah krn slang/gaul yg tidak ada di kamus (mis. "pulus" utk suap).

TIDAK memproses ulang SEMUA komentar (41rb+ dan terus bertambah) -- cuma 2
kategori di atas, LLM SEBAGAI PELENGKAP bukan pengganti lexicon (yg tetap
jalan instan+gratis utk semua komentar spt biasa). Hasil LLM disimpan di
kolom TERPISAH (llm_label dll), TIDAK menimpa label/score lexicon asli --
kedua bisa dibandingkan (`sentiment_agreement`).

Update 2026-07-18 (tie-breaker): kalau lexicon vs LLM1 TIDAK sepakat, LLM
KEDUA (provider beda, `google/gemma-4-31b-it:free` default) dipanggil sbg
suara ketiga -- `final_label` = mayoritas 2-dari-3 (None kalau 3 beda
semua). Kalau mayoritas MENGALAHKAN lexicon, kata2 dari komentar itu yg
belum ada di kamus lexicon dicatat sbg usulan di `lexicon_word_suggestions`
(BUKAN auto-ditambah ke app/ai/lexicon/data/*.txt -- kamus itu dipakai
LINTAS PLATFORM, perlu tinjau manual sebelum diadopsi).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.lexicon.service import _negative, _positive, _stopwords, _tokenize
from app.services.processing.normalizer import _detect_lang
from app.services.sentiment_agent import config as cfg
from app.services.sentiment_agent.openrouter_client import SentimentRateLimitError, classify_sentiment

logger = logging.getLogger(__name__)

# Jeda antar panggilan LLM -- pelajaran SAMA dari Discovery/Metadata Agent
# (model gratis OpenRouter gampang kena rate-limit 429 tanpa jeda).
SENTIMENT_CALL_DELAY_SECONDS = 2.0

# Kata terlalu pendek biasanya bukan kata sentimen (partikel, singkatan acak)
_MIN_SUGGESTION_WORD_LEN = 3


def _majority_label(*labels: str) -> str | None:
    """Mayoritas 2-dari-3. None kalau 3 label BEDA semua (genuinely ambigu,
    tidak ada suara terbanyak)."""
    counts = Counter(labels)
    top_label, top_count = counts.most_common(1)[0]
    return top_label if top_count >= 2 else None


def _extract_candidate_words(content: str) -> list[str]:
    """Kata dari komentar yg BELUM ada di kamus positif/negatif/stopword --
    kandidat usulan kata baru. Sengaja TIDAK coba tebak kata mana yg benar2
    bawa sentimen (butuh NLP lebih canggih) -- semua kandidat disimpan,
    kata NOISE (nama orang dll) akan punya evidence_count rendah krn jarang
    berulang, gampang disaring manusia saat tinjau (`min_evidence` di API)."""
    pos_set, neg_set, stop_set = _positive(), _negative(), _stopwords()
    seen: set[str] = set()
    candidates: list[str] = []
    for token in _tokenize(content):
        if token in pos_set or token in neg_set or token in stop_set or token in seen:
            continue
        if len(token) < _MIN_SUGGESTION_WORD_LEN:
            continue
        seen.add(token)
        candidates.append(token)
    return candidates


async def _upsert_word_suggestion(db: AsyncSession, word: str, polarity: str, example: str) -> None:
    await db.execute(text("""
        INSERT INTO lexicon_word_suggestions (id, word, suggested_polarity, evidence_count, example_comment)
        VALUES (:id, :word, :polarity, 1, :example)
        ON CONFLICT (word, suggested_polarity)
        DO UPDATE SET evidence_count = lexicon_word_suggestions.evidence_count + 1, updated_at = now()
    """), {"id": str(uuid.uuid4()), "word": word, "polarity": polarity, "example": example[:500]})


async def run_sentiment_agent(db: AsyncSession, keyword_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Entry point dipanggil worker Celery. Return ringkasan run.

    `keyword_id` (2026-07-19, opsional): kalau diisi, HANYA proses backlog
    komentar keyword itu -- dipakai utk prioritas manual (mis. "jokowi"
    perlu direview duluan drpd nunggu FIFO system-wide 111rb+ backlog).
    Kalau None (default, dipakai task Celery terjadwal), perilaku SAMA
    PERSIS seperti sebelumnya -- FIFO lintas SEMUA keyword, tidak berubah.
    """
    model = await cfg.get_model()
    api_key = await cfg.get_api_key()
    batch_size = await cfg.get_batch_size()
    tiebreaker_model = await cfg.get_tiebreaker_model()
    tiebreaker_api_key = await cfg.get_tiebreaker_api_key()

    processed = 0
    reviewed_by_llm = 0
    skipped_trusted = 0
    agreements = 0
    disagreements = 0
    tiebreaker_calls = 0
    lexicon_overturned = 0  # mayoritas (lexicon+LLM1+LLM2) MENGALAHKAN lexicon
    words_suggested = 0
    rate_limited = False  # ketemu 429 -> batch dihentikan, sisa komentar TIDAK ditandai
    errors: list[str] = []

    if not api_key:
        return {"status": "error", "message": "API key OpenRouter belum diatur (lihat PATCH /sentiment-agent/config)"}

    # ── Cari lexicon_analyses (komentar YouTube) yg BELUM direview LLM ───────
    # ORDER BY ASC (FIFO, backlog paling lama duluan) -- pola sama dgn Stage 2
    # Metadata Agent, jaga throughput > laju komentar baru masuk supaya
    # backlog genuinely mengecil, bukan cuma ngejar yg terbaru terus.
    keyword_filter = "AND p.keyword_id = :keyword_id" if keyword_id else ""
    rows = (await db.execute(text(f"""
        SELECT la.id, la.label, c.content
        FROM lexicon_analyses la
        JOIN comments c ON c.id = la.comment_id
        JOIN posts p ON p.id = c.post_id
        WHERE p.platform = 'youtube' AND la.llm_checked_at IS NULL {keyword_filter}
        ORDER BY la.created_at ASC
        LIMIT {batch_size}
    """), {"keyword_id": str(keyword_id)} if keyword_id else {})).mappings().all()

    if not rows:
        return {
            "status": "success", "processed": 0, "reviewed_by_llm": 0,
            "skipped_trusted": 0, "agreements": 0, "disagreements": 0,
            "tiebreaker_calls": 0, "lexicon_overturned": 0, "words_suggested": 0,
            "rate_limited": False, "errors": [],
        }

    now = datetime.now(timezone.utc)

    for r in rows:
        processed += 1
        la_id = r["id"]
        lexicon_label = r["label"]
        content = r["content"] or ""

        lang = _detect_lang(content)
        needs_review = (lang != "id") or (lexicon_label == "netral")

        if not needs_review:
            # Lexicon dipercaya (Bahasa Indonesia + sudah py opini
            # positif/negatif) -- TIDAK buang panggilan LLM, cuma catat
            # bahasanya + tandai sudah dicek supaya tidak diulang tiap run.
            skipped_trusted += 1
            await db.execute(text(
                "UPDATE lexicon_analyses SET detected_language=:lang, llm_checked_at=:now WHERE id=:id"
            ), {"lang": lang, "now": now, "id": str(la_id)})
            continue

        try:
            result = await classify_sentiment(api_key, model, content=content)
        except SentimentRateLimitError as exc:
            # Rate limit (429) = kegagalan SEMENTARA (limit harian/provider
            # congested) -- komentar INI TIDAK ditandai (di-retry run
            # berikutnya setelah limit reset), dan batch DIHENTIKAN
            # (panggilan berikutnya pasti gagal juga, percuma bakar jeda
            # 2 detik x sisa batch). Fix 2026-07-18: sebelumnya kasus ini
            # ikut jalur result=None di bawah -> komentar 'terbakar'
            # (ditandai sudah dicek tanpa label, dilewati PERMANEN).
            logger.warning("run_sentiment_agent: rate limit LLM primer, batch dihentikan: %s", exc)
            errors.append(f"rate-limit primer: {str(exc)[:150]}")
            rate_limited = True
            processed -= 1  # komentar ini batal diproses
            break
        except Exception as exc:
            logger.error("run_sentiment_agent: gagal klasifikasi id=%s: %s", la_id, exc)
            errors.append(f"{la_id}: {exc}")
            result = None
        finally:
            await asyncio.sleep(SENTIMENT_CALL_DELAY_SECONDS)

        if result is None:
            # Gagal panggil/parse LLM NON-rate-limit (mis. respons rusak) --
            # TETAP tandai sudah dicek (spt video hilang di Metadata Agent)
            # supaya TIDAK di-retry selamanya tiap run (buang kuota).
            await db.execute(text(
                "UPDATE lexicon_analyses SET detected_language=:lang, llm_checked_at=:now WHERE id=:id"
            ), {"lang": lang, "now": now, "id": str(la_id)})
            continue

        llm_label, _reason = result
        agree = (llm_label == lexicon_label)

        llm2_label: str | None = None
        final_label: str | None = None

        if agree:
            agreements += 1
            # Lexicon+LLM1 SUDAH sepakat -- itu sendiri mayoritas 2/2, tidak
            # perlu tie-breaker lagi.
            final_label = llm_label
        else:
            disagreements += 1
            if tiebreaker_api_key:
                try:
                    tb_result = await classify_sentiment(tiebreaker_api_key, tiebreaker_model, content=content)
                except SentimentRateLimitError as exc:
                    # Sama spt rate-limit primer: komentar TIDAK ditandai
                    # (hasil LLM1 utk komentar ini DIBUANG, diulang utuh run
                    # berikutnya -- benar > hemat), batch dihentikan.
                    logger.warning("run_sentiment_agent: rate limit tie-breaker, batch dihentikan: %s", exc)
                    errors.append(f"rate-limit tiebreaker: {str(exc)[:150]}")
                    rate_limited = True
                    processed -= 1
                    await asyncio.sleep(SENTIMENT_CALL_DELAY_SECONDS)
                    break
                except Exception as exc:
                    logger.error("run_sentiment_agent: gagal tie-breaker id=%s: %s", la_id, exc)
                    tb_result = None
                finally:
                    await asyncio.sleep(SENTIMENT_CALL_DELAY_SECONDS)

                if tb_result is not None:
                    llm2_label, _tb_reason = tb_result
                    tiebreaker_calls += 1
                    final_label = _majority_label(lexicon_label, llm_label, llm2_label)

                    if final_label is not None and final_label != lexicon_label:
                        # Mayoritas 2-dari-3 MENGALAHKAN lexicon -- lexicon
                        # kemungkinan besar kekurangan kata (slang dsb).
                        # Usulkan kata BARU dari komentar ini (BUKAN
                        # auto-tambah ke kamus, lihat docstring modul).
                        lexicon_overturned += 1
                        for word in _extract_candidate_words(content):
                            await _upsert_word_suggestion(db, word, final_label, content)
                            words_suggested += 1

        await db.execute(text("""
            UPDATE lexicon_analyses
            SET detected_language=:lang, llm_label=:llm_label, llm_model=:model,
                llm_checked_at=:now, sentiment_agreement=:agree,
                llm2_label=:llm2_label, llm2_model=:llm2_model, final_label=:final_label
            WHERE id=:id
        """), {
            "lang": lang, "llm_label": llm_label, "model": model,
            "now": now, "agree": agree, "id": str(la_id),
            "llm2_label": llm2_label,
            "llm2_model": tiebreaker_model if llm2_label else None,
            "final_label": final_label,
        })
        reviewed_by_llm += 1

    await db.commit()

    result = {
        "status": "success",
        "processed": processed,
        "reviewed_by_llm": reviewed_by_llm,
        "skipped_trusted": skipped_trusted,
        "agreements": agreements,
        "disagreements": disagreements,
        "tiebreaker_calls": tiebreaker_calls,
        "lexicon_overturned": lexicon_overturned,
        "words_suggested": words_suggested,
        "rate_limited": rate_limited,
        "errors": errors[:10],
    }
    logger.info("run_sentiment_agent: %s", result)
    return result
