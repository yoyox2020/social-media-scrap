"""agent_topic (2026-07-22, DIPERKUAT 2026-07-24) -- terima request
user, tentukan objective+topik, kirim ke agent_search. TIDAK boleh
ambil data (sesuai spec), murni penentuan topik saja.

BACA dari tabel `trend_recommendations` (2026-07-24, permintaan user
"semua agent topik dan agent search harusnya membaca dan mencari dari
tabel topik") -- kalau topik ini SUDAH terdaftar di sana, ambil
score/source aslinya (transparan di log+return, BUKAN cuma
menerima string apa adanya). Kalau BELUM terdaftar (mis. dipanggil
manual API dgn topik bebas, atau keyword fallback generik "viral"/
"fyp"/"trending" dari mode global_viral), TETAP jalan spt biasa --
TIDAK auto-mendaftarkan ke tabel (sengaja, spy keyword fallback
generik tidak ikut mencemari pool topik resmi -- pendaftaran topik
baru tetap harus lewat POST /trend-recommendations/manual yg eksplisit)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity_log import log_activity
from app.domain.trend_recommendations.models import TrendRecommendation

AGENT_NAME = "agent_topic"


async def determine_topic(db: AsyncSession, run_id: uuid.UUID, user_topic: str, platform: str = "youtube") -> dict:
    topic = user_topic.strip()

    existing = await db.scalar(
        select(TrendRecommendation)
        .where(TrendRecommendation.topic == topic)
        .order_by(TrendRecommendation.recommendation_date.desc())
        .limit(1)
    )
    if existing:
        score = existing.score
        source = existing.source
        known = True
    else:
        score = None
        source = None
        known = False

    objective = f"Cari & kumpulkan konten {platform} terbaru/trending terkait '{topic}'"

    await log_activity(
        db, run_id, AGENT_NAME, "determine_topic",
        f"Topik diterima: '{topic}' (platform={platform}, "
        + (f"terdaftar di trend_recommendations: score={score}, source={source}" if known else "TIDAK terdaftar di trend_recommendations -- topik ad-hoc/fallback")
        + ")",
        details={"topic": topic, "platform": platform, "objective": objective, "known_in_trend_recommendations": known, "score": score, "source": source},
    )

    return {"topic": topic, "platform": platform, "objective": objective, "known_in_trend_recommendations": known, "score": score, "source": source}
