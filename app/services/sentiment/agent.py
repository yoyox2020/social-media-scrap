"""Sentiment Agent -- opini KEDUA (LLM) utk komentar yg lexicon
(rule-based, khusus Bahasa Indonesia, app/services/sentiment/lexicon.py)
kemungkinan besar SALAH menilai:
  1. Komentar BUKAN Bahasa Indonesia -- lexicon Indonesia otomatis skor 0
     (netral) krn tidak ada kata yg cocok kamus, TERLEPAS dari sentimen
     aslinya (dibuktikan historis di kode lama: 67.5% komentar YouTube
     berlabel "netral", sample manual banyak berisi komentar asing yg
     jelas beropini).
  2. Komentar Indonesia yg lexicon bilang "netral" -- kasus paling sering
     salah krn slang/gaul yg tidak ada di kamus.

DIPORT dari `main` branch (PERNAH live-tested, [[project_sentiment_agent]])
+ DIPERLUAS: dulu HANYA YouTube (`WHERE p.platform = 'youtube'`), SEKARANG
6 platform yg py komentar (Facebook/Instagram/Threads/TikTok/Twitter/
YouTube -- News dikecualikan, TIDAK py thread komentar publik). Key LLM
SEKARANG lewat `rotation_key_bank` (pola SAMA dgn TikTok AI-summary,
app/agents/tiktok/struktur_data.py) -- BUKAN raw key statis di Redis lagi.

TIDAK memproses ulang SEMUA komentar -- cuma 2 kategori di atas, LLM
SEBAGAI PELENGKAP bukan pengganti lexicon (yg tetap jalan instan+gratis
utk semua komentar spt biasa, dipanggil INLINE saat komentar disimpan,
lihat save.py). Hasil LLM disimpan di kolom TERPISAH (llm_label dll),
TIDAK menimpa label/score lexicon asli.

Tie-breaker: kalau lexicon vs LLM1 TIDAK sepakat, LLM KEDUA (provider
BEDA) dipanggil sbg suara ketiga -- final_label = mayoritas 2-dari-3
(None kalau 3 beda semua). Kalau mayoritas MENGALAHKAN lexicon, kata2
dari komentar itu yg belum ada di kamus dicatat sbg usulan di
`lexicon_word_suggestions` (BUKAN auto-ditambah ke .txt -- kamus dipakai
LINTAS PLATFORM, perlu tinjau manual)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rotation_key_bank.service import get_working_key_for_agent, report_key_failure
from app.services.sentiment import config as cfg
from app.services.sentiment.lexicon import _negative, _positive, _stopwords, _tokenize, detect_language
from app.services.sentiment.openrouter_client import SentimentRateLimitError, classify_sentiment

logger = logging.getLogger(__name__)

# Platform yg py thread komentar publik -- News SENGAJA dikecualikan
# (artikel berita tidak py mekanisme komentar, lihat
# [[project_news_and_twitter_platform_rebuild]]).
COMMENT_PLATFORMS = ("facebook", "instagram", "threads", "tiktok", "twitter", "youtube")

SENTIMENT_CALL_DELAY_SECONDS = 2.0
_MIN_SUGGESTION_WORD_LEN = 3
FALLBACK_MODEL = cfg.DEFAULT_MODEL
FALLBACK_TIEBREAKER_MODEL = cfg.DEFAULT_TIEBREAKER_MODEL


def _majority_label(*labels: str) -> str | None:
    counts = Counter(labels)
    top_label, top_count = counts.most_common(1)[0]
    return top_label if top_count >= 2 else None


def _extract_candidate_words(content: str) -> list[str]:
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


async def run_sentiment_agent(db: AsyncSession) -> dict[str, Any]:
    """Entry point dipanggil worker Celery. Return ringkasan run."""
    batch_size = await cfg.get_batch_size()

    primary_key_info = await get_working_key_for_agent(db, cfg.AGENT_NAME_PRIMARY)
    if not primary_key_info or not primary_key_info.get("api_key"):
        return {"status": "error", "message": f"Belum ada key LLM utk agent '{cfg.AGENT_NAME_PRIMARY}' di rotation_key_bank"}
    api_key = primary_key_info["api_key"]
    model = primary_key_info.get("model") or FALLBACK_MODEL

    tiebreaker_key_info = await get_working_key_for_agent(db, cfg.AGENT_NAME_TIEBREAKER)
    tiebreaker_api_key = tiebreaker_key_info.get("api_key") if tiebreaker_key_info else None
    tiebreaker_model = (tiebreaker_key_info.get("model") if tiebreaker_key_info else None) or FALLBACK_TIEBREAKER_MODEL

    processed = 0
    reviewed_by_llm = 0
    skipped_trusted = 0
    agreements = 0
    disagreements = 0
    tiebreaker_calls = 0
    lexicon_overturned = 0
    words_suggested = 0
    rate_limited = False
    errors: list[str] = []

    platform_placeholders = ", ".join(f"'{p}'" for p in COMMENT_PLATFORMS)
    rows = (await db.execute(text(f"""
        SELECT la.id, la.label, c.content
        FROM lexicon_analyses la
        JOIN comments c ON c.id = la.comment_id
        JOIN posts p ON p.id = c.post_id
        WHERE p.platform IN ({platform_placeholders}) AND la.llm_checked_at IS NULL
        ORDER BY la.created_at ASC
        LIMIT {batch_size}
    """))).mappings().all()

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

        lang = detect_language(content)
        needs_review = (lang != "id") or (lexicon_label == "netral")

        if not needs_review:
            skipped_trusted += 1
            await db.execute(text(
                "UPDATE lexicon_analyses SET detected_language=:lang, llm_checked_at=:now WHERE id=:id"
            ), {"lang": lang, "now": now, "id": str(la_id)})
            continue

        try:
            result = await classify_sentiment(api_key, model, content=content)
        except SentimentRateLimitError as exc:
            logger.warning("run_sentiment_agent: rate limit LLM primer, coba rotasi key: %s", exc)
            new_key = await report_key_failure(db, cfg.AGENT_NAME_PRIMARY, str(exc)[:300])
            if new_key and new_key.get("api_key"):
                api_key = new_key["api_key"]
                model = new_key.get("model") or FALLBACK_MODEL
                try:
                    result = await classify_sentiment(api_key, model, content=content)
                except Exception:
                    result = None
            else:
                errors.append(f"rate-limit primer, tidak ada key pengganti: {str(exc)[:150]}")
                rate_limited = True
                processed -= 1
                break
        except Exception as exc:
            logger.error("run_sentiment_agent: gagal klasifikasi id=%s: %s", la_id, exc)
            errors.append(f"{la_id}: {exc}")
            result = None
        finally:
            await asyncio.sleep(SENTIMENT_CALL_DELAY_SECONDS)

        if result is None:
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
            final_label = llm_label
        else:
            disagreements += 1
            if tiebreaker_api_key:
                try:
                    tb_result = await classify_sentiment(tiebreaker_api_key, tiebreaker_model, content=content)
                except SentimentRateLimitError as exc:
                    logger.warning("run_sentiment_agent: rate limit tie-breaker: %s", exc)
                    errors.append(f"rate-limit tiebreaker: {str(exc)[:150]}")
                    tb_result = None
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
