"""
News Pipeline Service — simpan artikel (hasil app/integrations/firecrawl/news.py)
ke `posts` dengan `platform='news'` (app/shared/constants.py Platform.NEWS,
sudah diantisipasi sejak awal di skema, baru genuinely dipakai sekarang).

BEDA dari platform medsos lain: berita tidak punya thread komentar publik
terbuka seperti IG/FB/TikTok — jadi TIDAK ada baris `comments` yang diisi di
sini. Sentimen + NER dijalankan LANGSUNG di level POST (isi artikel itu
sendiri), lewat pipeline `analyze_post_task` yang sudah ada (generic per
post_id, tidak butuh kode baru untuk itu).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post

# Batas wajar 1 artikel -- jaga-jaga kalau markdown hasil scrape ternyata
# jauh lebih panjang dari artikel normal (misal salah scrape halaman index/
# arsip, bukan artikel tunggal). Tidak ada batas di kolom DB (Text), ini
# murni pagar keamanan.
MAX_CONTENT_CHARS = 20000


def compute_external_id(url: str) -> str:
    """Publik (dipakai juga oleh viral_discovery_scrape_service.py Fase 2
    untuk cek dedup SEBELUM scrape, hemat kuota Firecrawl)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


async def save_news_articles(
    db: AsyncSession,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Simpan artikel (tiap item hasil `scrape_article()`, minimal ada `url`+
    `content`) ke `posts` (platform='news'). Dedup by `external_id` (hash
    URL) — artikel yang sama tidak tersimpan dobel walau ditemukan lewat
    pencarian keyword berbeda atau dipanggil ulang.
    """
    if not articles:
        return {"articles_scraped": 0, "articles_saved": 0}

    valid_articles = [a for a in articles if a.get("url") and a.get("content")]
    ext_ids = [compute_external_id(a["url"]) for a in valid_articles]
    existing_ext_ids: set[str] = set()
    if ext_ids:
        existing_ext_ids = set((await db.scalars(
            select(Post.external_id).where(Post.platform == "news", Post.external_id.in_(ext_ids))
        )).all())

    saved_count = 0
    for article in valid_articles:
        url = article["url"]
        ext_id = compute_external_id(url)
        if ext_id in existing_ext_ids:
            continue

        post_obj = Post(
            id=uuid.uuid4(),
            external_id=ext_id,
            platform="news",
            content=article["content"][:MAX_CONTENT_CHARS],
            author=article.get("author"),
            url=url,
            # Tanggal publish ASLI dari metadata situs sumber (JSON-LD/OG tag),
            # lihat _parse_published_at() di app/integrations/firecrawl/news.py --
            # None kalau situs tidak menyediakannya (mis. halaman homepage/
            # kategori, bukan artikel tunggal) -- SENGAJA tidak di-fallback ke
            # collected_at, itu bukan waktu kejadian asli.
            published_at=article.get("published_at"),
            collected_at=datetime.now(timezone.utc),
            metadata_={
                "title":     article.get("title"),
                "image_url": article.get("image_url"),
                "source":    "firecrawl",
            },
            raw_data={"metadata": article["raw_metadata"]} if article.get("raw_metadata") else None,
        )
        db.add(post_obj)
        await db.flush()
        saved_count += 1
        existing_ext_ids.add(ext_id)

        # Sentimen (IndoBERT) + NER (GLiNER) — dispatch async ke worker-ai,
        # sama pola dengan Instagram/Facebook (butuh torch/transformers yang
        # cuma ada di container worker-ai). run_ner=True (beda dari platform
        # medsos yang sengaja False) karena isi artikel berita jauh lebih
        # bermanfaat untuk ekstraksi entitas (PERSON/ORG/GPE) dibanding
        # caption pendek medsos. run_embedding=False dulu (belum diminta).
        from app.workers.ai_worker import analyze_post_task
        analyze_post_task.delay(str(post_obj.id), run_sentiment=True, run_ner=True, run_embedding=False)

    await db.commit()

    return {"articles_scraped": len(articles), "articles_saved": saved_count}
