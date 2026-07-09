"""
News API endpoints — Fase 1 (search + sentimen dasar) + Fase 2 (trending).

GET /news/search?q=...        — cari artikel berita by keyword (2 tingkat:
                                 DB lokal -> search langsung Firecrawl kalau
                                 tidak ketemu), simpan + return isi lengkap.
GET /news/analysis/summary    — ringkasan sentimen SEMUA artikel berita
                                 tersimpan.
GET /news/trending             — artikel berita yang ditemukan pada tanggal
                                 tertentu (default hari ini) lewat pipeline
                                 discovery harian MANDIRI (Fase 2, lihat
                                 app/services/news/trend_scrape_service.py).

Beda dari platform medsos (Instagram/Facebook/TikTok/Twitter): berita tidak
punya konsep "akun" atau "komentar publik" — jadi tidak ada
GET /news/posts?username=..., tidak ada GET /news/comments. Sentimen
dihitung dari ISI ARTIKEL langsung (tabel `sentiments`, IndoBERT level-post,
label Inggris "positive"/"negative"/"neutral" -- BEDA dari `lexicon_analyses`
level-komentar yang labelnya Indonesia -- dipetakan ke Indonesia di respons
API ini demi konsistensi dengan endpoint lain).

`GET /news/trending` SENGAJA tidak dikaitkan ke topik trend_recommendations
tertentu (beda dari Instagram/Facebook/dst) — pipeline Fase 2 search LANGSUNG
ke Firecrawl pakai query generik ("berita trending hari ini", dst), TANPA
AI/LLM sama sekali (beda dari discovery topik medsos yang butuh reasoning
LLM buat cari akun) — jadi tidak ada satu "topik" tunggal per artikel.
"Trending" di sini artinya "artikel yang ditemukan hari ini", dikelompokkan
per tanggal koleksi. Pipeline ini MANDIRI TOTAL, tidak menyentuh atau
tergantung app/ai/llm/viral_discovery_service.py (AI viral discovery
Instagram/Facebook/TikTok/Twitter) sama sekali — supaya scraping medsos yang
sudah live tidak pernah berisiko terganggu oleh perubahan di fitur News.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/news", tags=["news"])

# sentiments.label (IndoBERT, level-post) tersimpan Inggris -- dipetakan ke
# Indonesia di sini demi konsistensi dengan endpoint lain (yang datanya dari
# lexicon_analyses level-komentar, labelnya sudah Indonesia dari sono).
_LABEL_EN_TO_ID = {"positive": "positif", "negative": "negatif", "neutral": "netral"}


# ─────────────────────────────────────────────────────────────────────────────
# GET /news/search — cari artikel berita by keyword
# ─────────────────────────────────────────────────────────────────────────────

async def _build_news_items(db: AsyncSession, post_rows) -> list[dict]:
    """Gabung post + sentimen jadi satu ringkasan per artikel."""
    if not post_rows:
        return []
    post_ids = [r["id"] for r in post_rows]

    sentiment_rows = (await db.execute(text("""
        SELECT post_id, label, score FROM sentiments WHERE post_id = ANY(:ids)
    """), {"ids": post_ids})).mappings().all()
    sentiment_by_post = {
        s["post_id"]: {"label": _LABEL_EN_TO_ID.get(s["label"], s["label"]), "score": s["score"]}
        for s in sentiment_rows
    }

    items = []
    for r in post_rows:
        meta = r["metadata"] or {}
        items.append({
            "post_id":      str(r["id"]),
            "title":        meta.get("title"),
            "content":      r["content"],
            "author":       r["author"],
            "url":          r["url"],
            "image_url":    meta.get("image_url"),
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            "collected_at": r["collected_at"].isoformat() if r["collected_at"] else None,
            "sentiment":    sentiment_by_post.get(r["id"]),
        })
    return items


@router.get("/search", response_model=dict, summary="Cari artikel berita berdasarkan keyword")
async def search_news(
    q: str = Query(..., min_length=1, max_length=200, description="Keyword pencarian berita"),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari artikel berita berdasarkan judul/isi.

    **Alur (2 tingkat):**
    1. Cari di `posts.content`/`metadata->>'title'` (platform='news') yang
       sudah tersimpan dari pencarian sebelumnya.
    2. Kalau tidak ketemu -> search LANGSUNG ke Firecrawl (maks 5 hasil demi
       kontrol biaya, terlepas dari `limit`), scrape isi lengkap tiap URL
       hasil, simpan, lalu return.
    """
    q_clean = q.strip()
    if not q_clean:
        raise HTTPException(status_code=422, detail="Keyword tidak boleh kosong")

    # ── 1. Cari di posts.content / metadata->>'title' ──────────────────────────
    post_rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'news'
          AND (content ILIKE :kw OR metadata->>'title' ILIKE :kw)
        ORDER BY published_at DESC NULLS LAST, collected_at DESC
        LIMIT :limit
    """), {"kw": f"%{q_clean}%", "limit": limit})).mappings().all()

    if post_rows:
        items = await _build_news_items(db, post_rows)
        return build_success_response({"query": q_clean, "source": "database", "total": len(items), "items": items})

    # ── 2. Tidak ketemu -> search LANGSUNG ke Firecrawl ─────────────────────────
    from app.domain.scrape_runs.models import ScrapeRun
    from app.integrations.firecrawl.news import scrape_article, search_news_by_keyword
    from app.services.news.pipeline_service import save_news_articles

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text=f"search:{q_clean}", platform="news", api_source="firecrawl",
        status="running", triggered_by="manual_api", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

    try:
        search_results = await search_news_by_keyword(q_clean, max_results=5)
    except Exception as exc:
        scrape_run.status = "failed"
        scrape_run.error_message = str(exc)[:1000]
        scrape_run.finished_at = datetime.now(timezone.utc)
        scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
        await db.commit()
        return build_success_response({
            "query": q_clean, "source": "not_found", "total": 0, "items": [],
            "message": (
                "Tidak ditemukan artikel di database, DAN search langsung ke "
                f"Firecrawl gagal ({exc})."
            ),
        })

    articles = []
    for r in search_results:
        url = r.get("url")
        if not url:
            continue
        article = await scrape_article(url)
        if article:
            articles.append(article)

    save_result = await save_news_articles(db, articles)

    scrape_run.status = "success" if save_result["articles_saved"] > 0 else "failed"
    scrape_run.videos_fetched = save_result["articles_scraped"]
    scrape_run.videos_new = save_result["articles_saved"]
    scrape_run.finished_at = datetime.now(timezone.utc)
    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()
    await db.commit()

    if not articles:
        return build_success_response({
            "query": q_clean, "source": "not_found", "total": 0, "items": [],
            "message": (
                "Tidak ditemukan artikel di database maupun via search langsung "
                "ke Firecrawl (search berhasil tapi tidak ada URL yang bisa di-scrape)."
            ),
        })

    urls = [a["url"] for a in articles]
    fresh_rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'news' AND url = ANY(:urls)
        ORDER BY collected_at DESC
    """), {"urls": urls})).mappings().all()

    items = await _build_news_items(db, fresh_rows)
    return build_success_response({
        "query": q_clean, "source": "scraped_now", "total": len(items),
        "note": "Sentimen/entitas diproses async (Celery) — mungkin belum muncul kalau baru saja discrape.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /news/analysis/summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analysis/summary", response_model=dict,
            summary="Ringkasan sentimen + entitas trending artikel berita")
async def get_news_analysis_summary(
    top_n: int = Query(default=15, ge=1, le=50, description="Jumlah entitas trending ditampilkan"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ringkasan SEMUA artikel berita yang tersimpan (platform='news') — 2 lapis
    analisis, bukan cuma sentimen:

    1. **Sentimen level-artikel** (tabel `sentiments`, IndoBERT) — **PERHATIKAN
       KETERBATASANNYA**: berita mengikuti konvensi objektif (piramida
       terbalik), jadi sentimen TEKS sering cuma mencerminkan valensi
       PERISTIWA yang diliput (bencana/kriminal = skor negatif walau
       laporannya netral), BUKAN sikap/bias media itu sendiri. Media secara
       sistematis lebih banyak meliput peristiwa negatif ("negativity bias"
       dalam jurnalisme, lihat riset Soroka & McAdams 2015) — jadi persentase
       negatif yang tinggi TIDAK BOLEH ditafsirkan sebagai "media bersikap
       negatif", lebih tepat dibaca sebagai "peristiwa yang diliput hari ini
       banyak yang negatif". Interpretasikan dengan hati-hati, JANGAN
       dijadikan satu-satunya sinyal.
    2. **Entitas trending** (tabel `entities`, NER/GLiNER — PERSON/ORGANIZATION/
       LOCATION/DATE/EVENT) — "siapa/apa yang sedang dibicarakan" lintas
       artikel, sinyal yang lebih langsung actionable untuk media-monitoring
       dibanding sentimen level-dokumen (pendekatan entity-level/aspect-based
       lebih disarankan literatur sentiment analysis dibanding document-level
       untuk teks berita, lihat Liu 2012 "Sentiment Analysis and Opinion
       Mining"). **Catatan kualitas data**: NER kadang menangkap noise dari
       teks navigasi situs (menu/link), bukan cuma isi artikel murni — cek
       manual kalau ada entitas yang terlihat ganjil (misal fragmen URL).

    Beda dari platform medsos lain: sentimen di sini dihitung dari ISI
    ARTIKEL langsung — berita tidak punya komentar publik terbuka seperti
    IG/FB/TikTok.
    """
    row = (await db.execute(text("""
        SELECT
            count(DISTINCT p.id) AS total_articles,
            count(s.id)          AS total_analyzed,
            count(*) FILTER (WHERE s.label = 'positive') AS positif,
            count(*) FILTER (WHERE s.label = 'negative') AS negatif,
            count(*) FILTER (WHERE s.label = 'neutral')  AS netral
        FROM posts p
        LEFT JOIN sentiments s ON s.post_id = p.id
        WHERE p.platform = 'news'
    """))).mappings().first()

    total = row["total_articles"] or 0
    analyzed = row["total_analyzed"] or 0

    def _pct(count: int, base: int) -> float:
        return round(count / base * 100, 1) if base else 0.0

    # ── Entitas trending (NER) — "siapa/apa yang dibicarakan", lintas artikel ──
    entity_rows = (await db.execute(text("""
        SELECT e.text, e.entity_type, count(DISTINCT e.post_id) AS mentions
        FROM entities e
        JOIN posts p ON p.id = e.post_id
        WHERE p.platform = 'news'
        GROUP BY e.text, e.entity_type
        ORDER BY mentions DESC, e.text ASC
        LIMIT :top_n
    """), {"top_n": top_n})).mappings().all()

    entity_type_rows = (await db.execute(text("""
        SELECT e.entity_type, count(*) AS total
        FROM entities e
        JOIN posts p ON p.id = e.post_id
        WHERE p.platform = 'news'
        GROUP BY e.entity_type
        ORDER BY total DESC
    """))).mappings().all()

    # ── Sumber (outlet) — keragaman liputan, dari domain URL ────────────────────
    source_rows = (await db.execute(text("""
        SELECT regexp_replace(url, '^https?://(www\\.)?([^/]+).*$', '\\2') AS domain, count(*) AS total
        FROM posts
        WHERE platform = 'news' AND url IS NOT NULL
        GROUP BY domain
        ORDER BY total DESC
    """))).mappings().all()

    return build_success_response({
        "total_articles": total,
        "total_analyzed": analyzed,
        "fully_analyzed": analyzed == total,
        "sentiment": {
            "positif": {"count": row["positif"], "percentage": _pct(row["positif"], analyzed)},
            "negatif": {"count": row["negatif"], "percentage": _pct(row["negatif"], analyzed)},
            "netral":  {"count": row["netral"],  "percentage": _pct(row["netral"], analyzed)},
            "caveat": (
                "Sentimen berita mencerminkan valensi PERISTIWA yang diliput, "
                "bukan sikap media -- jangan tafsirkan persentase negatif "
                "tinggi sebagai bias media. Lihat docstring endpoint."
            ),
        },
        "trending_entities": {
            "by_type": {r["entity_type"]: r["total"] for r in entity_type_rows},
            "top": [
                {"text": r["text"], "type": r["entity_type"], "mentions": r["mentions"]}
                for r in entity_rows
            ],
        },
        "sources": [
            {"domain": r["domain"], "articles": r["total"]}
            for r in source_rows
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /news/trending
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending", response_model=dict, summary="Artikel berita yang ditemukan pada tanggal tertentu")
async def get_news_trending(
    collection_date: date | None = Query(default=None, description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Artikel berita yang ditemukan pada tanggal tertentu lewat pipeline
    discovery harian MANDIRI (Fase 2, `app/services/news/trend_scrape_service.py`,
    jadwal `settings.news_discovery_schedule_hour/minute`) — search LANGSUNG
    ke Firecrawl (query generik, TANPA AI/LLM), jadi SELALU aktif tiap hari,
    tidak tergantung provider AI discovery medsos apa pun.

    Beda dari `GET /instagram/trending` dkk: TIDAK dikaitkan ke topik
    trend_recommendations tertentu — lihat catatan di docstring modul ini.
    """
    target_date = collection_date or datetime.now(timezone.utc).date()

    post_rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'news' AND collected_at::date = :target_date
        ORDER BY collected_at DESC
    """), {"target_date": target_date})).mappings().all()

    items = await _build_news_items(db, post_rows)

    return build_success_response({
        "date": target_date.isoformat(),
        "total_articles": len(items),
        "source": "news_daily_discovery (mandiri, lihat docstring endpoint)",
        "items": items,
    })
