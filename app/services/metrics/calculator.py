"""
MetricsCalculator — semua rumus metrik ada di sini.

Untuk tambah platform baru:
  1. Buat adapter di adapters/<platform>.py
  2. Daftarkan di ADAPTER_REGISTRY di bawah
  3. Tidak perlu ubah kode lain
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.services.metrics.adapters.base import PlatformAdapter
from app.services.metrics.adapters.tiktok import TikTokAdapter
from app.services.metrics.adapters.twitter import TwitterAdapter
from app.services.metrics.adapters.youtube import YouTubeAdapter

# ── Registry: tambah platform baru di sini ───────────────────────────────────
ADAPTER_REGISTRY: dict[str, PlatformAdapter] = {
    "youtube": YouTubeAdapter(),
    "tiktok":  TikTokAdapter(),
    "twitter": TwitterAdapter(),
    # "instagram": InstagramAdapter(),  # belum -- views/shares tidak tersedia dari provider (lihat analisa engagement)
    # "facebook":  FacebookAdapter(),   # belum -- views/shares tidak tersedia dari provider (lihat analisa engagement)
    # "news":      NewsAdapter(),       # belum relevan -- artikel tidak punya konsep engagement sosial
}


def get_adapter(platform: str) -> PlatformAdapter:
    return ADAPTER_REGISTRY.get(platform, PlatformAdapter())


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class RawPostStats:
    post_id: uuid.UUID
    platform: str
    author: str | None
    metadata: dict
    comment_count_db: int


@dataclass
class SentimentCounts:
    positif: int = 0
    negatif: int = 0
    netral: int = 0

    @property
    def total(self) -> int:
        return self.positif + self.negatif + self.netral

    def score(self) -> float:
        """Sentiment Score = ((Positif - Negatif) / Total) × 100"""
        if self.total == 0:
            return 0.0
        return round((self.positif - self.negatif) / self.total * 100, 2)


# ── Rumus Metrik ─────────────────────────────────────────────────────────────

def calc_exposure(post_stats: list[RawPostStats]) -> int:
    """Exposure = Total Impression (sum seluruh views)."""
    total = 0
    for p in post_stats:
        adapter = get_adapter(p.platform)
        total += adapter.extract_views(p.metadata)
    return total


def calc_reach(post_stats: list[RawPostStats]) -> int:
    """
    Reach = Total akun unik yang terjangkau.
    Proxy: jumlah channel/author unik yang mempublish konten tentang keyword ini.
    Catatan: reach viewer sesungguhnya = exposure (views), tapi untuk social listening
    yang lebih tepat adalah unique creator/channel yang membahas topik.
    """
    return len({p.author for p in post_stats if p.author})


def calc_engagement(post_stats: list[RawPostStats]) -> tuple[int, dict]:
    """
    Engagement = Total Like + Comment + Share + Save + Reply + Click.
    Return: (total_engagement, breakdown_per_komponen)
    """
    breakdown = {"likes": 0, "comments": 0, "shares": 0, "saves": 0, "replies": 0, "clicks": 0}
    for p in post_stats:
        adapter = get_adapter(p.platform)
        bd = adapter.engagement_breakdown(p.metadata, p.comment_count_db)
        for key in breakdown:
            breakdown[key] += bd.get(key, 0)
    total = sum(breakdown.values())
    return total, breakdown


def calc_engagement_rate(engagement: int, reach: int) -> float:
    """Engagement Rate = (Engagement ÷ Reach) × 100%."""
    if reach == 0:
        return 0.0
    return round(engagement / reach * 100, 2)


def calc_sentiment_score(counts: SentimentCounts) -> float:
    """Sentiment Score = ((Positif - Negatif) / Total) × 100."""
    return counts.score()


def calc_sov(keyword_mentions: int, total_mentions: int) -> float:
    """Share of Voice = (Mention Keyword Ini ÷ Total Mention Semua Keyword) × 100%."""
    if total_mentions == 0:
        return 0.0
    return round(keyword_mentions / total_mentions * 100, 2)


def calc_mention_growth(current: int, previous: int) -> float:
    """Mention Growth = ((Sekarang - Sebelumnya) / Sebelumnya) × 100%."""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 2)


# ── DB Query Helpers ──────────────────────────────────────────────────────────

# Platform yang keyword_id-nya bisa diandalkan (FK terisi saat post disimpan).
# Non-anggota (tiktok/twitter/instagram/facebook/news) SEMUA keyword_id-nya
# NULL -- Smart Search menghubungkan post<->keyword lewat ILIKE teks saat
# QUERY (lihat app/services/search_topics/tier_search.py), bukan FK saat
# simpan. Ini fakta arsitektur yang sudah ada sebelumnya, bukan bug baru.
KEYWORD_ID_RELIABLE_PLATFORMS: frozenset[str] = frozenset({"youtube"})


def _needs_text_match(platforms: list[str]) -> bool:
    """True kalau ada platform non-reliable diminta (atau platform tidak
    difilter sama sekali, yg berarti bisa mencakup platform non-reliable)."""
    if not platforms:
        return True
    return any(p not in KEYWORD_ID_RELIABLE_PLATFORMS for p in platforms)


async def _keyword_condition(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
):
    """Kondisi filter keyword. Kalau semua platform yg diminta reliable
    (saat ini cuma YouTube), cabang ini IDENTIK dgn `Post.keyword_id.in_(...)`
    lama -- tidak ada query tambahan, perilaku existing tidak berubah.
    Kalau ada platform lain, tambahkan pencocokan ILIKE (pola AND-per-kata
    sama dgn tier_search._word_and_clause) khusus utk platform non-reliable,
    sementara platform reliable tetap disaring lewat keyword_id asli."""
    if not keyword_ids:
        return None
    if not _needs_text_match(platforms):
        return Post.keyword_id.in_(keyword_ids)

    texts = [k for k in (await db.scalars(
        select(Keyword.keyword).where(Keyword.id.in_(keyword_ids))
    )).all() if k]
    if not texts:
        return Post.keyword_id.in_(keyword_ids)

    text_match = or_(*[
        and_(*[Post.content.ilike(f"%{w}%") for w in (t.split() or [t])])
        for t in texts
    ])
    return or_(
        and_(Post.platform.in_(KEYWORD_ID_RELIABLE_PLATFORMS), Post.keyword_id.in_(keyword_ids)),
        and_(~Post.platform.in_(KEYWORD_ID_RELIABLE_PLATFORMS), text_match),
    )


async def fetch_post_stats(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[RawPostStats]:
    """Ambil semua post beserta jumlah komentar per post."""
    filters = []
    kw_cond = await _keyword_condition(db, keyword_ids, platforms)
    if kw_cond is not None:
        filters.append(kw_cond)
    if platforms:
        filters.append(Post.platform.in_(platforms))
    if date_from:
        filters.append(Post.published_at >= date_from)
    if date_to:
        filters.append(Post.published_at <= date_to)

    posts = (await db.scalars(select(Post).where(*filters))).all()
    if not posts:
        return []

    # Hitung komentar per post dalam satu query
    post_ids = [p.id for p in posts]
    comment_counts_rows = await db.execute(
        select(Comment.post_id, func.count(Comment.id).label("cnt"))
        .where(Comment.post_id.in_(post_ids))
        .group_by(Comment.post_id)
    )
    comment_map: dict[uuid.UUID, int] = {row.post_id: row.cnt for row in comment_counts_rows}

    return [
        RawPostStats(
            post_id=p.id,
            platform=p.platform,
            author=p.author,
            metadata=p.metadata_ or {},
            comment_count_db=comment_map.get(p.id, 0),
        )
        for p in posts
    ]


async def fetch_sentiment_counts(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
) -> SentimentCounts:
    """Ambil jumlah positif/negatif/netral dari lexicon_analyses."""
    post_filter = []
    kw_cond = await _keyword_condition(db, keyword_ids, platforms)
    if kw_cond is not None:
        post_filter.append(kw_cond)
    if platforms:
        post_filter.append(Post.platform.in_(platforms))
    if date_from:
        post_filter.append(Post.published_at >= date_from)
    if date_to:
        post_filter.append(Post.published_at <= date_to)

    rows = await db.execute(
        select(LexiconAnalysis.label, func.count(LexiconAnalysis.id))
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(*post_filter)
        .group_by(LexiconAnalysis.label)
    )
    counts = SentimentCounts()
    for label, cnt in rows.all():
        if label == "positif":
            counts.positif = cnt
        elif label == "negatif":
            counts.negatif = cnt
        elif label == "netral":
            counts.netral = cnt
    return counts


async def fetch_mention_count(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
) -> int:
    filters = []
    kw_cond = await _keyword_condition(db, keyword_ids, platforms)
    if kw_cond is not None:
        filters.append(kw_cond)
    if platforms:
        filters.append(Post.platform.in_(platforms))
    if date_from:
        filters.append(Post.published_at >= date_from)
    if date_to:
        filters.append(Post.published_at <= date_to)
    return (await db.scalar(select(func.count(Post.id)).where(*filters))) or 0


# ── Main: hitung semua metrik sekaligus ──────────────────────────────────────

async def compute_metrics(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    compare_date_from: datetime | None = None,
    compare_date_to: datetime | None = None,
    all_keyword_ids: list[uuid.UUID] | None = None,
) -> dict:
    """
    Hitung semua 7 metrik sekaligus.
    all_keyword_ids dipakai untuk SOV (pembanding seluruh keyword di project/topik).
    """

    # ── Data periode ini
    post_stats = await fetch_post_stats(db, keyword_ids, platforms, date_from, date_to)
    sentiment = await fetch_sentiment_counts(db, keyword_ids, platforms, date_from, date_to)
    mention_current = len(post_stats)

    # ── Kalkulasi
    exposure = calc_exposure(post_stats)
    reach = calc_reach(post_stats)
    engagement, eng_breakdown = calc_engagement(post_stats)
    engagement_rate = calc_engagement_rate(engagement, reach)
    sentiment_score = calc_sentiment_score(sentiment)

    # ── SOV
    sov = None
    if all_keyword_ids:
        total_mentions = await fetch_mention_count(db, all_keyword_ids, platforms, date_from, date_to)
        sov = calc_sov(mention_current, total_mentions)

    # ── Mention Growth (bandingkan dengan periode sebelumnya)
    mention_growth = None
    if compare_date_from and compare_date_to:
        mention_prev = await fetch_mention_count(db, keyword_ids, platforms, compare_date_from, compare_date_to)
        mention_growth = calc_mention_growth(mention_current, mention_prev)

    return {
        "exposure": {
            "value": exposure,
            "label": "Total Impression",
            "description": "Total tayangan seluruh postingan",
        },
        "reach": {
            "value": reach,
            "label": "Reach",
            "description": "Total akun unik (channel/creator) yang membahas topik ini",
        },
        "engagement": {
            "value": engagement,
            "label": "Engagement",
            "description": "Total Like + Komentar + Share + Save + Reply + Klik",
            "breakdown": eng_breakdown,
        },
        "engagement_rate": {
            "value": engagement_rate,
            "label": "Engagement Rate",
            "unit": "%",
            "description": "(Engagement ÷ Reach) × 100%",
        },
        "sentiment_score": {
            "value": sentiment_score,
            "label": "Sentiment Score",
            "unit": "%",
            "description": "((Positif − Negatif) ÷ Total Percakapan) × 100",
            "detail": {
                "positif": sentiment.positif,
                "negatif": sentiment.negatif,
                "netral": sentiment.netral,
                "total": sentiment.total,
            },
        },
        "sov": {
            "value": sov,
            "label": "Share of Voice",
            "unit": "%",
            "description": "(Mention keyword ini ÷ Total mention semua keyword) × 100%",
            "available": sov is not None,
        },
        "mention_growth": {
            "value": mention_growth,
            "label": "Mention Growth",
            "unit": "%",
            "description": "((Mention periode ini − periode sebelumnya) ÷ periode sebelumnya) × 100%",
            "available": mention_growth is not None,
        },
        "mentions": {
            "value": mention_current,
            "label": "Total Mentions",
            "description": "Total postingan yang membahas keyword ini",
        },
    }
