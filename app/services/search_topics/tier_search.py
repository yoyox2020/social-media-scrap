"""
Tier-1 search utk Smart Search (app/api/v1/topic_search.py +
app/services/search_topics/rescan_service.py) -- ILIKE ke `posts.content`
DAN `comments.content` (join `comments.post_id = posts.id`), BUKAN
`Post.keyword_id`.

KENAPA BUKAN keyword_id: cuma pipeline YouTube yang pernah mengisi
`Post.keyword_id` -- semua alur trend_recommendations-driven (Facebook/
Instagram/TikTok/Twitter, baik interaktif maupun daily scrape) menyimpan
post dengan `keyword_id=None`, cuma terhubung lewat `Post.author`/
`trend_recommendations.topic` text. Kalau tier-1 tetap pakai keyword_id,
hasil pencarian akan diam-diam kosong utk hampir semua platform selain
YouTube walau datanya sebenarnya ADA di `posts`. Pola ILIKE ini sama
persis dengan `get_trend_feed()` di app/api/v1/trend_discovery/router.py
yang sudah live-verified sesi ini.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _word_and_clause(column: str, keyword_text: str, params: dict, prefix: str) -> str:
    """`col ILIKE %w1% AND col ILIKE %w2% AND ...` utk tiap kata di
    `keyword_text`, BUKAN satu ILIKE atas frasa utuh -- keyword smart
    search selalu multi-kata (mis. "kebakaran hutan kalimantan 2026"),
    dan konten post/komentar hasil scrape ASLI (artikel berita, caption,
    dll) nyaris tidak pernah mengandung frasa itu APA ADANYA/berurutan
    persis, walau semua katanya memang ada di teks. ILIKE frasa-utuh
    live-tested balik 0 hasil utk keyword yang barusan sukses ditemukan
    tier-3 (Firecrawl) & tersimpan -- AND per-kata live-tested balik hasil
    yang benar. `prefix` bikin nama parameter unik per kolom supaya fungsi
    ini aman dipanggil >1 kali dalam satu query (lihat find_comments_by_keyword
    yang butuh kolom post & comment sekaligus)."""
    words = [w for w in keyword_text.split() if w]
    if not words:
        words = [keyword_text]
    clauses = []
    for i, w in enumerate(words):
        pname = f"{prefix}_{i}"
        params[pname] = f"%{w}%"
        clauses.append(f"{column} ILIKE :{pname}")
    return "(" + " AND ".join(clauses) + ")"


def _extract_view_count(meta: dict) -> int:
    raw_views = meta.get("views", meta.get("view_count", 0))
    try:
        return int(str(raw_views).replace(",", "").split()[0]) if raw_views else 0
    except (ValueError, IndexError):
        return 0


async def find_posts_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
    limit: int = 10,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Post yang `content`-nya mengandung semua kata di `keyword_text`
    (AND per-kata, lihat _word_and_clause), opsional difilter platform &
    terbaru dulu. `since` FILTER PAKAI `collected_at` (kapan KITA nemu/
    nyimpan post ini), BUKAN `published_at` (kapan artikel/post itu ASLINYA
    dibuat) -- dipakai rescan_service.py sbg cek freshness "apa kita udah
    nemu sesuatu baru2 ini", bukan "apa kontennya baru diterbitkan". Artikel
    berita hasil Firecrawl search sering berumur bulanan (published_at lama)
    walau baru KITA temukan hari ini -- kalau filter pakai published_at,
    cooldown rescan tidak akan pernah ke-skip & tier-3 kepanggil ulang
    tiap hari terus-menerus (live-tested: menyebabkan tier3_triggered=1
    walau post yang sama baru disimpan 2 menit sebelumnya)."""
    platform_clause = "AND platform = ANY(:platforms)" if platforms else ""
    since_clause = "AND collected_at >= :since" if since else ""
    params: dict = {"limit": limit}
    match_clause = _word_and_clause("content", keyword_text, params, "kw")
    if platforms:
        params["platforms"] = platforms
    if since:
        params["since"] = since

    rows = (await db.execute(text(f"""
        SELECT id, platform, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE {match_clause}
          {platform_clause}
          {since_clause}
        ORDER BY collected_at DESC
        LIMIT :limit
    """), params)).mappings().all()

    results = []
    for p in rows:
        meta = p["metadata"] or {}
        results.append({
            "id": str(p["id"]),
            "platform": p["platform"],
            "title": p["content"],
            "author": p["author"],
            "url": p["url"],
            "view_count": _extract_view_count(meta),
            "likes": meta.get("likes", 0),
            "published_at": p["published_at"].isoformat() if p["published_at"] else None,
            "collected_at": p["collected_at"].isoformat() if p["collected_at"] else None,
            "thumbnail_url": meta.get("thumbnail") or meta.get("photo_url") or meta.get("image_url") or "",
        })
    return results


async def find_comments_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
    limit: int = 10,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Comment yang `content`-nya mengandung `keyword_text`, lewat join ke
    `posts` (utk platform + url balik ke post induk, comment sendiri tidak
    punya permalink)."""
    platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    since_clause = "AND c.published_at >= :since" if since else ""
    params: dict = {"limit": limit}
    match_clause = _word_and_clause("c.content", keyword_text, params, "kw")
    if platforms:
        params["platforms"] = platforms
    if since:
        params["since"] = since

    rows = (await db.execute(text(f"""
        SELECT c.id, p.platform, c.content, c.author, p.url, c.published_at
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE {match_clause}
          {platform_clause}
          {since_clause}
        ORDER BY c.published_at DESC NULLS LAST
        LIMIT :limit
    """), params)).mappings().all()

    return [
        {
            "id": str(r["id"]),
            "platform": r["platform"],
            "content": r["content"],
            "author": r["author"],
            "url": r["url"],
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
        }
        for r in rows
    ]


async def get_sentiment_summary_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
) -> dict[str, Any]:
    """Ringkasan sentimen KOMENTAR (`lexicon_analyses`, label Indonesia)
    utk semua post yang cocok `keyword_text` -- "bagaimana reaksi warganet
    ke post-post soal topik ini", bukan cuma komentar yang literally
    mengandung kata itu. Pengganti versi `Post.keyword_id`-based lama di
    topic_search.py (cuma menangkap data YouTube)."""
    platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    params: dict = {}
    match_clause = _word_and_clause("p.content", keyword_text, params, "kw")
    if platforms:
        params["platforms"] = platforms

    rows = (await db.execute(text(f"""
        SELECT la.label, count(la.id) AS cnt
        FROM lexicon_analyses la
        JOIN comments c ON c.id = la.comment_id
        JOIN posts p ON p.id = c.post_id
        WHERE {match_clause}
          {platform_clause}
        GROUP BY la.label
    """), params)).mappings().all()

    summary = {"positif": 0, "negatif": 0, "netral": 0}
    total = 0
    for r in rows:
        label = r["label"]
        if label in summary:
            summary[label] = r["cnt"]
            total += r["cnt"]

    if total > 0:
        dominant = max(summary, key=summary.get)
        return {
            "total_analyzed": total,
            "positif": {"count": summary["positif"], "pct": round(summary["positif"] * 100 / total, 1)},
            "negatif": {"count": summary["negatif"], "pct": round(summary["negatif"] * 100 / total, 1)},
            "netral":  {"count": summary["netral"],  "pct": round(summary["netral"]  * 100 / total, 1)},
            "dominant": dominant,
        }
    return {"total_analyzed": 0}


async def count_posts_and_comments_by_keyword(
    db: AsyncSession,
    keyword_text: str,
    platforms: list[str] | None,
) -> tuple[int, int]:
    """Hitung total post + komentar yang cocok `keyword_text` (utk dashboard
    list/detail topik) -- lebih murah drpd narik semua baris cuma utk
    dihitung panjangnya."""
    platform_clause = "AND platform = ANY(:platforms)" if platforms else ""
    post_params: dict = {}
    post_match = _word_and_clause("content", keyword_text, post_params, "kw")
    if platforms:
        post_params["platforms"] = platforms

    total_posts = (await db.execute(text(f"""
        SELECT count(*) FROM posts WHERE {post_match} {platform_clause}
    """), post_params)).scalar() or 0

    comment_platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    comment_params: dict = {}
    comment_match = _word_and_clause("p.content", keyword_text, comment_params, "kw")
    if platforms:
        comment_params["platforms"] = platforms

    total_comments = (await db.execute(text(f"""
        SELECT count(*)
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        WHERE {comment_match}
          {comment_platform_clause}
    """), comment_params)).scalar() or 0

    return total_posts, total_comments


def _multi_keyword_or_clause(column: str, keywords: list[str], params: dict) -> str:
    """OR antar BEBERAPA keyword (topik bisa punya >1 keyword), tiap keyword
    sendiri tetap AND per-kata lewat _word_and_clause -- dipakai grafik tren
    harian (get_daily_post_counts/get_daily_sentiment_counts) yang perlu
    menghitung SEMUA keyword topik sekaligus, bukan satu-satu."""
    clauses = [_word_and_clause(column, kw, params, f"kw{i}") for i, kw in enumerate(keywords)]
    return "(" + " OR ".join(clauses) + ")"


async def get_daily_post_counts(
    db: AsyncSession,
    keywords: list[str],
    platforms: list[str] | None,
    days: int = 7,
) -> dict[str, dict[str, int]]:
    """Jumlah post per hari per platform, `days` hari terakhir (termasuk hari
    ini), utk SEMUA keyword topik sekaligus (OR antar keyword) -- dipakai
    GET /search/topics/{id}/trend-graph. Return {tanggal_iso: {platform: count}},
    hari yang genuinely 0 post TIDAK muncul sebagai key (pemanggil isi 0
    sendiri saat menyusun array 7 hari penuh)."""
    if not keywords:
        return {}
    platform_clause = "AND platform = ANY(:platforms)" if platforms else ""
    params: dict = {"days": days}
    match_clause = _multi_keyword_or_clause("content", keywords, params)
    if platforms:
        params["platforms"] = platforms

    rows = (await db.execute(text(f"""
        SELECT date_trunc('day', collected_at)::date AS day, platform, count(*) AS cnt
        FROM posts
        WHERE {match_clause}
          {platform_clause}
          AND collected_at >= (CURRENT_DATE - (:days - 1) * INTERVAL '1 day')
        GROUP BY day, platform
        ORDER BY day
    """), params)).mappings().all()

    result: dict[str, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["day"].isoformat(), {})[r["platform"]] = r["cnt"]
    return result


async def get_daily_sentiment_counts(
    db: AsyncSession,
    keywords: list[str],
    platforms: list[str] | None,
    days: int = 7,
) -> dict[str, dict[str, int]]:
    """Sentimen komentar (`lexicon_analyses` -- INI yang genuinely terisi,
    99%+ komentar, BEDA dari tabel `sentiments`/IndoBERT yang nyaris kosong,
    lihat get_sentiment_summary_by_keyword() di atas) per hari, `days` hari
    terakhir, utk SEMUA keyword topik sekaligus. Return
    {tanggal_iso: {label: count}}."""
    if not keywords:
        return {}
    platform_clause = "AND p.platform = ANY(:platforms)" if platforms else ""
    params: dict = {"days": days}
    match_clause = _multi_keyword_or_clause("p.content", keywords, params)
    if platforms:
        params["platforms"] = platforms

    rows = (await db.execute(text(f"""
        SELECT date_trunc('day', la.created_at)::date AS day, la.label, count(*) AS cnt
        FROM lexicon_analyses la
        JOIN comments c ON c.id = la.comment_id
        JOIN posts p ON p.id = c.post_id
        WHERE {match_clause}
          {platform_clause}
          AND la.created_at >= (CURRENT_DATE - (:days - 1) * INTERVAL '1 day')
        GROUP BY day, la.label
        ORDER BY day
    """), params)).mappings().all()

    result: dict[str, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["day"].isoformat(), {})[r["label"]] = r["cnt"]
    return result
