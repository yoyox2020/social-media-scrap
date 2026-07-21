"""
MetricsCalculator — semua rumus metrik ada di sini.

Untuk tambah platform baru:
  1. Buat adapter di adapters/<platform>.py
  2. Daftarkan di ADAPTER_REGISTRY di bawah
  3. Tidak perlu ubah kode lain
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.comments.models import Comment
from app.domain.keywords.models import Keyword
from app.domain.posts.models import Post
from app.domain.youtube_analysis.models import LexiconAnalysis
from app.services.metrics.adapters.base import PlatformAdapter
from app.services.metrics.adapters.facebook import FacebookAdapter
from app.services.metrics.adapters.instagram import InstagramAdapter
from app.services.metrics.adapters.tiktok import TikTokAdapter
from app.services.metrics.adapters.twitter import TwitterAdapter
from app.services.metrics.adapters.youtube import YouTubeAdapter

# ── Registry: tambah platform baru di sini ───────────────────────────────────
ADAPTER_REGISTRY: dict[str, PlatformAdapter] = {
    "youtube":   YouTubeAdapter(),
    "tiktok":    TikTokAdapter(),
    "twitter":   TwitterAdapter(),
    "facebook":  FacebookAdapter(),    # views/shares unavailable -- provider tidak pernah kirim
    "instagram": InstagramAdapter(),   # sama + sengaja abaikan sisa data views lama (lihat adapters/instagram.py)
    # "news":      NewsAdapter(),       # belum relevan -- artikel tidak punya konsep engagement sosial
}


_unregistered_platform_warned: set[str] = set()


def get_adapter(platform: str) -> PlatformAdapter:
    adapter = ADAPTER_REGISTRY.get(platform)
    if adapter is not None:
        return adapter
    # Platform TIDAK terdaftar (typo di kolom posts.platform, atau platform
    # baru yg lupa didaftarkan ke ADAPTER_REGISTRY) -- adapter default punya
    # `unavailable=[]` (KOSONG), jadi diam2 KELIHATAN spt "semua field
    # tersedia" walau tidak ada satu pun yg benar2 dipetakan (metadata.get()
    # generic langsung return 0 kalau key tidak ada). Warning SEKALI per
    # platform (bukan tiap post) supaya kelihatan di log tanpa bikin spam,
    # ditemukan 2026-07-18 saat audit "apakah semua platform genuinely
    # tercakup di /metrics/summary".
    if platform not in _unregistered_platform_warned:
        logger.warning(
            "get_adapter: platform %r TIDAK terdaftar di ADAPTER_REGISTRY -- "
            "pakai adapter default (semua field dianggap 'tersedia' scr generic, "
            "kemungkinan besar hasilnya 0 semua krn nama field tidak match). "
            "Cek typo di posts.platform atau daftarkan adapter baru.",
            platform,
        )
        _unregistered_platform_warned.add(platform)
    return PlatformAdapter()


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


def calc_per_platform_breakdown(post_stats: list[RawPostStats]) -> dict[str, dict]:
    """Rincian exposure/reach/engagement PER PLATFORM -- permintaan user
    2026-07-18 ("overview" perlu breakdown per-platform, sebelumnya
    /metrics/summary cuma balikin angka gabungan tanpa rincian per
    platform sama sekali). Reuse `post_stats` yg SUDAH di-fetch (satu
    query yg sama dgn metrik gabungan) -- TIDAK ada query DB tambahan,
    cuma group-by di Python."""
    groups: dict[str, list[RawPostStats]] = {}
    for p in post_stats:
        groups.setdefault(p.platform, []).append(p)

    result = {}
    for platform, stats in groups.items():
        engagement, breakdown = calc_engagement(stats)
        result[platform] = {
            "mentions": len(stats),
            "exposure": calc_exposure(stats),
            "reach": calc_reach(stats),
            "engagement": engagement,
            "engagement_breakdown": breakdown,
        }
    return result


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


async def fetch_keyword_texts(db: AsyncSession, keyword_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Fetch SEKALI teks keyword (id->text) -- dipakai caller yg butuh
    panggil _keyword_condition() BERKALI-KALI (mis. /metrics/sov per-keyword,
    /metrics/topic breakdown_per_keyword) supaya tidak query Keyword.keyword
    ULANG tiap iterasi (N+1, ditemukan 2026-07-18 saat audit performa)."""
    if not keyword_ids:
        return {}
    rows = await db.execute(select(Keyword.id, Keyword.keyword).where(Keyword.id.in_(keyword_ids)))
    return {row.id: row.keyword for row in rows.all() if row.keyword}


async def _keyword_condition(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    keyword_texts: dict[uuid.UUID, str] | None = None,
):
    """Kondisi filter keyword. Kalau semua platform yg diminta reliable
    (saat ini cuma YouTube), cabang ini IDENTIK dgn `Post.keyword_id.in_(...)`
    lama -- tidak ada query tambahan, perilaku existing tidak berubah.
    Kalau ada platform lain, tambahkan pencocokan ILIKE (pola AND-per-kata
    sama dgn tier_search._word_and_clause) khusus utk platform non-reliable,
    sementara platform reliable tetap disaring lewat keyword_id asli.

    `keyword_texts` opsional -- kalau caller SUDAH fetch duluan (lihat
    fetch_keyword_texts()), reuse itu drpd query lagi di sini (hemat 1
    query tiap panggilan berulang dgn keyword_ids yg sama/subset)."""
    if not keyword_ids:
        return None
    if not _needs_text_match(platforms):
        return Post.keyword_id.in_(keyword_ids)

    if keyword_texts is not None:
        texts = [keyword_texts[kid] for kid in keyword_ids if keyword_texts.get(kid)]
    else:
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
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> list[RawPostStats]:
    """Ambil semua post beserta jumlah komentar per post."""
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    filters = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)

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
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> SentimentCounts:
    """Ambil jumlah positif/negatif/netral dari lexicon_analyses."""
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    post_filter = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)

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
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> int:
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    filters = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)
    return (await db.scalar(select(func.count(Post.id)).where(*filters))) or 0


# ── Drill-down: daftar data MENTAH di balik tiap angka metrik ─────────────────
# Permintaan user 2026-07-18: "user menyorot mention harus jelas sumber
# datanya darimana dan bisa diarahkan ke detail mention" -- SEMUA fungsi di
# bawah reuse _keyword_condition() yg SAMA PERSIS dgn yg dipakai hitung
# angka summary (compute_metrics()), supaya daftar yg ditampilkan DIJAMIN
# konsisten dgn angka agregatnya (bukan query terpisah yg bisa diam2 beda
# hasil).

def _post_filters(
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
    kw_cond,
) -> list:
    """Dipakai SEMUA fungsi query di file ini (fetch_post_stats,
    fetch_sentiment_counts, fetch_mention_count, drill-down dkk) --
    SATU tempat definisi filter tanggal, supaya konsisten.

    Filter tanggal pakai `COALESCE(published_at, collected_at)` -- SAMA
    PERSIS dgn cara /metrics/trend (endpoint router.py, date_trunc query)
    mengelompokkan post per hari. Sebelumnya fungsi2 di file ini HANYA
    cek `published_at` (tanpa fallback), padahal live-verified 24 post
    YouTube + 81 post News punya `published_at IS NULL` -- post2 itu
    DIAM2 KE-EXCLUDE dari drill-down/summary manapun yg pakai rentang
    tanggal, walau trend chart TETAP menghitungnya (via collected_at).
    Ditemukan 2026-07-18 saat user minta drill-down utk grafik tren harian
    -- tanpa fix ini, total drill-down utk 1 hari BISA BEDA dari angka
    "Mentions: N" yg ditampilkan trend chart utk hari yg sama."""
    filters = []
    if kw_cond is not None:
        filters.append(kw_cond)
    if platforms:
        filters.append(Post.platform.in_(platforms))
    published_or_collected = func.coalesce(Post.published_at, Post.collected_at)
    if date_from:
        filters.append(published_or_collected >= date_from)
    if date_to:
        filters.append(published_or_collected <= date_to)
    return filters


SORTABLE_ENGAGEMENT_COMPONENTS: frozenset[str] = frozenset(
    {"views", "likes", "comments", "shares", "saves", "replies", "clicks"}
)


async def fetch_post_detail_page(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    limit: int,
    keyword_texts: dict[uuid.UUID, str] | None = None,
    sort_by: str | None = None,
) -> tuple[list[dict], int]:
    """Drill-down utk metrik mentions/exposure/engagement -- daftar POST
    mentah (bukan cuma jumlah) yg menyusun angka itu, tiap item bawa `id`
    (Post.id) + `url` (link asli ke platform) supaya frontend bisa arahkan
    user ke post aslinya.

    `sort_by=None` (default): urut published_at TERBARU dulu, pagination
    SQL langsung (efisien, tidak perlu load semua post).

    `sort_by` salah satu SORTABLE_ENGAGEMENT_COMPONENTS (mis. "likes"):
    permintaan user 2026-07-18 "klik segmen Likes di grafik komposisi ->
    tampilkan post PALING BANYAK di-like duluan". Nilai komponen ini nama
    field-nya BEDA per platform (mis. TikTok "collects" utk saves, Twitter
    "retweets" utk shares -- lihat adapters/*.py), jadi TIDAK BISA di-ORDER
    BY langsung di SQL (bukan kolom polos) -- fetch id+platform+metadata_
    SEMUA post yg match (ringan, tanpa kolom besar spt raw_data), ekstrak
    via adapter yg SAMA dipakai hitung breakdown, urutkan di Python, BARU
    ambil detail lengkap utk 1 halaman yg diminta. Pola fetch-semua ini
    SAMA dgn yg sudah dipakai compute_metrics() (fetch_post_stats() jg
    tidak paginated), jadi bukan beban baru drpd yg sudah ada."""
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    filters = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)

    total = (await db.scalar(select(func.count(Post.id)).where(*filters))) or 0
    if total == 0:
        return [], 0

    if sort_by in SORTABLE_ENGAGEMENT_COMPONENTS:
        rows = (await db.execute(select(Post.id, Post.platform, Post.metadata_).where(*filters))).all()
        comment_map_all: dict[uuid.UUID, int] = {}
        if sort_by == "comments":
            all_ids = [r.id for r in rows]
            comment_count_rows = await db.execute(
                select(Comment.post_id, func.count(Comment.id).label("cnt"))
                .where(Comment.post_id.in_(all_ids)).group_by(Comment.post_id)
            )
            comment_map_all = {r.post_id: r.cnt for r in comment_count_rows}

        def _sort_key(row) -> int:
            adapter = get_adapter(row.platform)
            metadata = row.metadata_ or {}
            if sort_by == "comments":
                return comment_map_all.get(row.id, 0)
            return getattr(adapter, f"extract_{sort_by}")(metadata)

        ranked_ids = [r.id for r in sorted(rows, key=_sort_key, reverse=True)]
        page_ids = ranked_ids[(page - 1) * limit: (page - 1) * limit + limit]
        posts_by_id = {p.id: p for p in (await db.scalars(select(Post).where(Post.id.in_(page_ids)))).all()}
        posts = [posts_by_id[pid] for pid in page_ids if pid in posts_by_id]
    else:
        posts = (await db.scalars(
            select(Post).where(*filters)
            .order_by(Post.published_at.desc().nullslast())
            .offset((page - 1) * limit).limit(limit)
        )).all()

    if not posts:
        return [], total

    post_ids = [p.id for p in posts]
    comment_rows = await db.execute(
        select(Comment.post_id, func.count(Comment.id).label("cnt"))
        .where(Comment.post_id.in_(post_ids)).group_by(Comment.post_id)
    )
    comment_map = {row.post_id: row.cnt for row in comment_rows}

    items = []
    for p in posts:
        adapter = get_adapter(p.platform)
        metadata = p.metadata_ or {}
        comment_count = comment_map.get(p.id, 0)
        items.append({
            "id": str(p.id),
            "external_id": p.external_id,
            "platform": p.platform,
            "url": p.url,
            "title": p.title or p.content,
            "author": p.author,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "views": adapter.extract_views(metadata),
            "engagement": adapter.extract_engagement(metadata, comment_count),
            "engagement_breakdown": adapter.engagement_breakdown(metadata, comment_count),
        })
    return items, total


async def fetch_reach_detail_page(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    limit: int,
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> tuple[list[dict], int]:
    """Drill-down utk metrik reach -- Reach = jumlah AKUN UNIK (bukan post),
    jadi daftar detailnya per-author (bukan per-post): berapa post yg
    dibuat + total exposure gabungan akun itu, diurut post TERBANYAK dulu."""
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    filters = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)
    filters.append(Post.author.isnot(None))

    total = (await db.scalar(select(func.count(func.distinct(Post.author))).where(*filters))) or 0
    rows = (await db.execute(
        select(Post.author, Post.platform, func.count(Post.id).label("post_count"))
        .where(*filters)
        .group_by(Post.author, Post.platform)
        .order_by(func.count(Post.id).desc())
        .offset((page - 1) * limit).limit(limit)
    )).all()

    return [
        {"author": row.author, "platform": row.platform, "post_count": row.post_count}
        for row in rows
    ], total


async def fetch_sentiment_detail_page(
    db: AsyncSession,
    keyword_ids: list[uuid.UUID],
    platforms: list[str],
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    limit: int,
    label_filter: str | None = None,
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> tuple[list[dict], int]:
    """Drill-down utk metrik sentiment_score -- daftar KOMENTAR mentah
    (bukan cuma jumlah positif/negatif/netral) beserta post asal (id+url)
    supaya user bisa lihat KONTEKS komentar itu ditulis di post mana."""
    kw_cond = await _keyword_condition(db, keyword_ids, platforms, keyword_texts)
    post_filter = _post_filters(keyword_ids, platforms, date_from, date_to, kw_cond)

    base_query = (
        select(Comment, LexiconAnalysis, Post)
        .join(LexiconAnalysis, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(*post_filter)
    )
    if label_filter:
        base_query = base_query.where(LexiconAnalysis.label == label_filter)

    total = (await db.scalar(
        select(func.count(LexiconAnalysis.id))
        .select_from(LexiconAnalysis)
        .join(Comment, LexiconAnalysis.comment_id == Comment.id)
        .join(Post, Comment.post_id == Post.id)
        .where(*post_filter, LexiconAnalysis.label == label_filter if label_filter else True)
    )) or 0

    rows = (await db.execute(
        base_query.order_by(Comment.published_at.desc().nullslast())
        .offset((page - 1) * limit).limit(limit)
    )).all()

    items = [
        {
            "comment_id": str(comment.id),
            "content": comment.content,
            "author": comment.author,
            "label": (analysis.final_label or analysis.label),
            "sentiment_source": "llm_reviewed" if analysis.final_label else "lexicon_only",
            "post_id": str(post.id),
            "post_url": post.url,
            "post_title": post.title or post.content,
            "platform": post.platform,
            "published_at": comment.published_at.isoformat() if comment.published_at else None,
        }
        for comment, analysis, post in rows
    ]
    return items, total


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
    keyword_texts: dict[uuid.UUID, str] | None = None,
) -> dict:
    """
    Hitung semua 7 metrik sekaligus.
    all_keyword_ids dipakai untuk SOV (pembanding seluruh keyword di project/topik).
    keyword_texts opsional -- kalau caller SUDAH fetch duluan (mis. loop
    breakdown_per_keyword di /metrics/topic), reuse itu drpd fetch lagi di
    sini. Kalau None, di-fetch SEKALI di bawah utk seluruh gabungan
    keyword_ids+all_keyword_ids -- ganti dari SEBELUMNYA sampai 4 query
    identik terpisah (masing2 dari fetch_post_stats/fetch_sentiment_counts/
    fetch_mention_count x2) jadi cuma 1 (ditemukan 2026-07-18 saat audit
    performa /metrics/*)."""
    if keyword_texts is None:
        combined_ids = list({*keyword_ids, *(all_keyword_ids or [])})
        keyword_texts = await fetch_keyword_texts(db, combined_ids)

    # ── Data periode ini
    post_stats = await fetch_post_stats(db, keyword_ids, platforms, date_from, date_to, keyword_texts)
    sentiment = await fetch_sentiment_counts(db, keyword_ids, platforms, date_from, date_to, keyword_texts)
    mention_current = len(post_stats)

    # ── Kalkulasi
    exposure = calc_exposure(post_stats)
    reach = calc_reach(post_stats)
    engagement, eng_breakdown = calc_engagement(post_stats)
    engagement_rate = calc_engagement_rate(engagement, reach)
    sentiment_score = calc_sentiment_score(sentiment)
    per_platform = calc_per_platform_breakdown(post_stats)

    # ── SOV
    sov = None
    if all_keyword_ids:
        total_mentions = await fetch_mention_count(db, all_keyword_ids, platforms, date_from, date_to, keyword_texts)
        sov = calc_sov(mention_current, total_mentions)

    # ── Mention Growth (bandingkan dengan periode sebelumnya)
    mention_growth = None
    if compare_date_from and compare_date_to:
        mention_prev = await fetch_mention_count(db, keyword_ids, platforms, compare_date_from, compare_date_to, keyword_texts)
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
        "per_platform": per_platform,
    }
