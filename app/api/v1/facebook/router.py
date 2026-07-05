"""
Facebook API endpoints.

GET  /facebook/posts?username=X    — scrape (via Apify) + ambil post dari page/profil manapun (maks 10)
GET  /facebook/posts/search?q=...  — cari post yang SUDAH discrape berdasarkan keyword (bukan username)
GET  /facebook/search?q=keyword    — cari PAGE Facebook (Meta Graph API, TERBUKTI mati — lihat docs/flow scrape/flow-scrap-facebook.md)
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.config import settings
from app.shared.utils import build_success_response

router = APIRouter(prefix="/facebook", tags=["facebook"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/posts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts", response_model=dict, summary="Scrape + ambil post Facebook dari page/username")
async def get_facebook_posts(
    username: str = Query(..., min_length=1, max_length=200, description="Username / Page ID Facebook"),
    max_posts: int = Query(default=10, ge=1, le=10, description="Jumlah post (maks 10)"),
    max_comments: int = Query(default=20, ge=0, le=50, description="Jumlah komentar per post (maks 50)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape post dari Facebook page/profil manapun via provider abstraction
    (Apify — lihat app/services/facebook/providers/), simpan ke DB, analisis
    sentimen post (IndoBERT) + komentar (lexicon).

    **Kenapa Apify, bukan Meta Graph API resmi:** token Meta terverifikasi
    live cuma bisa akses Page yang dikelola sendiri — diblokir total untuk
    page publik manapun di luar itu (butuh "Page Public Content Access" yang
    harus di-approve Meta, lihat docs/flow scrape/flow-scrap-facebook.md).
    Apify terbukti bisa untuk page publik manapun.

    - Jika data sudah ada dan `force_refresh=false` → return dari DB
    - Jika belum ada atau `force_refresh=true` → scrape via Apify
    - Dedup akun-per-hari otomatis (skip panggil Apify kalau sudah discrape hari ini)

    **Yang di-scrape per post:**
    - Caption, likes, komentar, hashtag (→ entities), sentimen post+komentar

    **Response:**
    - `page_info` : followers (kalau ada dari hasil scrape)
    - `items`     : list post dengan nested `comments` + `sentiment_summary`
    - `sentiment` : distribusi global sentimen komentar
    """
    identifier = username.strip().lstrip("@")

    existing_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM posts WHERE platform = 'facebook' AND author = :author"),
        {"author": identifier},
    ) or 0

    scrape_result: dict | None = None
    if existing_count == 0 or force_refresh:
        from app.services.facebook.pipeline_service import scrape_facebook_posts_via_provider
        scrape_result = await scrape_facebook_posts_via_provider(
            db=db,
            identifier=identifier,
            max_posts=max_posts,
            max_comments=max_comments,
            keyword_id=None,
        )

    # ── Ambil posts dari DB ───────────────────────────────────────────────────
    rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'facebook' AND author = :author
        ORDER BY published_at DESC NULLS LAST
        LIMIT :limit
    """), {"author": identifier, "limit": max_posts})).mappings().all()

    page_info: dict = {"username": identifier}
    if rows and (rows[0]["metadata"] or {}).get("followers"):
        page_info["followers"] = rows[0]["metadata"]["followers"]

    # ── Batch-fetch komentar ──────────────────────────────────────────────────
    post_ids = [str(r["id"]) for r in rows]
    comments_by_post: dict[str, list] = {pid: [] for pid in post_ids}
    all_labels: list[str] = []
    total_per_post: dict[str, int] = {}

    if post_ids and max_comments > 0:
        ids_sql = ", ".join(f"'{pid}'" for pid in post_ids)
        cmt_rows = (await db.execute(text(f"""
            SELECT c.id, c.content, c.author, c.post_id::text AS post_id,
                   la.label AS sentiment, la.score
            FROM comments c
            LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
            WHERE c.post_id::text IN ({ids_sql})
            ORDER BY c.created_at DESC
        """))).mappings().all()

        for cr in cmt_rows:
            pid = cr["post_id"]
            total_per_post[pid] = total_per_post.get(pid, 0) + 1
            if cr["sentiment"]:
                all_labels.append(cr["sentiment"])
            bucket = comments_by_post.setdefault(pid, [])
            if len(bucket) < max_comments:
                bucket.append({
                    "id":        str(cr["id"]),
                    "content":   cr["content"],
                    "author":    cr["author"],
                    "sentiment": cr["sentiment"],
                    "score":     round(float(cr["score"]), 3) if cr["score"] is not None else None,
                })

    # ── Build items ───────────────────────────────────────────────────────────
    items = []
    for i, r in enumerate(rows):
        pid = str(r["id"])
        meta = r["metadata"] or {}
        vid_cmts = comments_by_post.get(pid, [])
        vid_lbls = [c["sentiment"] for c in vid_cmts if c["sentiment"]]
        sc = Counter(vid_lbls)
        total_sc = sum(sc.values())

        items.append({
            "rank":          i + 1,
            "post_id":       r["external_id"],
            "url":           r["url"] or f"https://www.facebook.com/{r['external_id']}",
            "message":       r["content"] or "",
            "author":        r["author"],
            "likes":         meta.get("likes", 0),
            "shares":        meta.get("shares", 0),
            "comment_count": total_per_post.get(pid, meta.get("comments", 0)),
            "thumbnail":     meta.get("thumbnail", ""),
            "published_at":  r["published_at"].isoformat() if r["published_at"] else None,
            "collected_at":  r["collected_at"].isoformat() if r["collected_at"] else None,
            "sentiment_summary": {
                lbl: {
                    "count":      sc.get(lbl, 0),
                    "percentage": round(sc.get(lbl, 0) / total_sc * 100, 1) if total_sc else 0.0,
                }
                for lbl in ["positif", "negatif", "netral"]
            },
            "comments": vid_cmts,
        })

    # ── Sentimen global ───────────────────────────────────────────────────────
    counter = Counter(all_labels)
    total_analyzed = sum(counter.values())
    total_cmts = sum(total_per_post.values())
    sentiment_dist = {
        lbl: {
            "count":      counter.get(lbl, 0),
            "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
        }
        for lbl in ["positif", "negatif", "netral"]
    }

    return build_success_response({
        "platform":  "facebook",
        "username":  identifier,
        "scrape":    {
            "posts_scraped": scrape_result.get("posts_scraped", 0) if scrape_result else 0,
            "posts_new":     scrape_result.get("posts_saved", 0) if scrape_result else 0,
            "provider_used": scrape_result.get("provider_used") if scrape_result else None,
            "errors":        scrape_result.get("errors", []) if scrape_result else [],
        } if scrape_result else None,
        "page_info": page_info,
        "stats": {
            "total_posts":    len(items),
            "total_comments": total_cmts,
            "total_analyzed": total_analyzed,
            "coverage_pct":   round(total_analyzed / total_cmts * 100, 1) if total_cmts else 0.0,
        },
        "sentiment": {
            **sentiment_dist,
            "dominant":       counter.most_common(1)[0][0] if counter else "netral",
            "total_analyzed": total_analyzed,
        },
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/posts/search — cari post berdasarkan keyword/hashtag (BUKAN username)
# ─────────────────────────────────────────────────────────────────────────────

async def _build_fb_search_items(db: AsyncSession, post_rows) -> list[dict]:
    """Gabung post + komentar + sentimen (post & komentar) jadi satu ringkasan per post."""
    if not post_rows:
        return []
    post_ids = [r["id"] for r in post_rows]

    sentiment_rows = (await db.execute(text("""
        SELECT post_id, label, score FROM sentiments WHERE post_id = ANY(:ids)
    """), {"ids": post_ids})).mappings().all()
    sentiment_by_post = {s["post_id"]: {"label": s["label"], "score": s["score"]} for s in sentiment_rows}

    comment_rows = (await db.execute(text("""
        SELECT c.post_id, c.content, c.author, c.published_at, la.label AS lexicon_label
        FROM comments c
        LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
        WHERE c.post_id = ANY(:ids)
        ORDER BY c.created_at DESC
    """), {"ids": post_ids})).mappings().all()

    comments_by_post: dict = {}
    for c in comment_rows:
        comments_by_post.setdefault(c["post_id"], []).append({
            "content":      c["content"],
            "author":       c["author"],
            "published_at": c["published_at"].isoformat() if c["published_at"] else None,
            "sentiment":    c["lexicon_label"] or "netral",
        })

    items = []
    for r in post_rows:
        meta = r["metadata"] or {}
        post_comments = comments_by_post.get(r["id"], [])
        cmt_dist = Counter(c["sentiment"] for c in post_comments)
        items.append({
            "post_id":        str(r["id"]),
            "external_id":    r["external_id"],
            "author":         r["author"],
            "message":        r["content"],
            "url":            r["url"],
            "likes":          meta.get("likes", 0),
            "comments_count": meta.get("comments", 0),
            "published_at":   r["published_at"].isoformat() if r["published_at"] else None,
            "sentiment": {
                "post":             sentiment_by_post.get(r["id"]),
                "comments_summary": {lbl: cmt_dist.get(lbl, 0) for lbl in ["positif", "negatif", "netral"]},
            },
            "comments": post_comments,
        })
    return items


@router.get("/posts/search", response_model=dict,
            summary="Cari post Facebook yang sudah di-scrape berdasarkan keyword/hashtag")
async def search_facebook_posts(
    q: str = Query(..., min_length=1, max_length=200, description="Keyword atau hashtag (boleh pakai # atau tidak)"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari post Facebook berdasarkan isi CAPTION atau HASHTAG yang sudah
    tersimpan dari scrape sebelumnya — BUKAN cari page baru (itu
    `GET /facebook/search`, dan itu sudah mati sejak Meta hapus endpoint-nya).

    **Alur:**
    1. Cari di `posts.content` dan tabel `entities` (hashtag).
    2. Kalau tidak ketemu: cari topik yang cocok di `trend_recommendations`
       (dibaca saja, tidak mengubah logikanya). Kalau ketemu topik dengan
       akun Facebook → scrape SEKARANG via Apify, langsung return hasilnya.
    3. Kalau tidak ketemu post maupun topik sama sekali → Apify tidak bisa
       cari-by-keyword sendiri, jadi tidak ada akun yang bisa discrape;
       submit manual kalau tahu akunnya via `POST /trend-recommendations`.
    """
    q_clean = q.strip().lstrip("#")
    if not q_clean:
        raise HTTPException(status_code=422, detail="Keyword tidak boleh kosong")

    # ── 1. Cari di posts.content atau hashtag (entities) ───────────────────────
    post_rows = (await db.execute(text("""
        SELECT DISTINCT p.id, p.external_id, p.content, p.author, p.url,
               p.published_at, p.metadata
        FROM posts p
        LEFT JOIN entities e ON e.post_id = p.id AND e.entity_type = 'HASHTAG'
        WHERE p.platform = 'facebook'
          AND (p.content ILIKE :kw OR e.text ILIKE :kw_exact)
        ORDER BY p.published_at DESC NULLS LAST
        LIMIT :limit
    """), {"kw": f"%{q_clean}%", "kw_exact": q_clean, "limit": limit})).mappings().all()

    if post_rows:
        items = await _build_fb_search_items(db, post_rows)
        return build_success_response({"query": q_clean, "source": "database", "total": len(items), "items": items})

    # ── 2. Tidak ketemu -> cari topik cocok di trend_recommendations ───────────
    from app.domain.trend_recommendations.models import TrendRecommendation

    candidate_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.topic.ilike(f"%{q_clean}%"))
        .order_by(TrendRecommendation.score.desc())
    )).all()

    matched_topic = None
    matched_identifier = None
    for t in candidate_topics:
        for acc in t.related_accounts or []:
            if acc.get("platform") == "facebook" and acc.get("username"):
                matched_topic = t
                matched_identifier = acc["username"]
                break
        if matched_topic:
            break

    if not matched_topic:
        return build_success_response({
            "query": q_clean, "source": "not_found", "total": 0, "items": [],
            "message": (
                "Tidak ditemukan post maupun topik terkait keyword ini. Apify tidak "
                "bisa cari akun by keyword — kalau tahu akun Facebook-nya, submit "
                "manual via POST /trend-recommendations."
            ),
        })

    # ── 3. Ketemu topik -> scrape sekarang via Apify ────────────────────────────
    from app.domain.scrape_runs.models import ScrapeRun
    from app.services.facebook.pipeline_service import scrape_facebook_posts_via_provider

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text=f"search:{matched_identifier}", platform="facebook", api_source="apify",
        status="running", triggered_by="manual_api", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

    scrape_result = await scrape_facebook_posts_via_provider(
        db=db, identifier=matched_identifier, max_posts=5, max_comments=5, keyword_id=None,
    )

    scrape_run.status = "success" if scrape_result.get("posts_scraped", 0) > 0 else "failed"
    scrape_run.api_source = scrape_result.get("provider_used") or "apify"
    scrape_run.videos_fetched = scrape_result.get("posts_scraped", 0)
    scrape_run.videos_new = scrape_result.get("posts_saved", 0)
    scrape_run.error_message = "; ".join(scrape_result.get("errors", [])[:3]) or None
    scrape_run.finished_at = datetime.now(timezone.utc)
    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

    if scrape_run.status == "success":
        matched_topic.status = "used"

    await db.commit()

    # ── Re-query post yang baru discrape untuk akun ini ────────────────────────
    fresh_rows = (await db.execute(text("""
        SELECT p.id, p.external_id, p.content, p.author, p.url, p.published_at, p.metadata
        FROM posts p
        WHERE p.platform = 'facebook' AND p.author = :identifier
        ORDER BY p.published_at DESC NULLS LAST
        LIMIT :limit
    """), {"identifier": matched_identifier, "limit": limit})).mappings().all()

    items = await _build_fb_search_items(db, fresh_rows)
    return build_success_response({
        "query": q_clean, "source": "scraped_now", "total": len(items),
        "topic": matched_topic.topic, "facebook_identifier": matched_identifier,
        "note": "Sentimen post baru diproses async (Celery) — mungkin belum muncul kalau baru saja discrape.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/search
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=dict, summary="Cari page Facebook berdasarkan keyword")
async def search_facebook_pages(
    q: str = Query(..., min_length=1, max_length=200, description="Keyword pencarian"),
    limit: int = Query(default=10, ge=1, le=25, description="Jumlah hasil (maks 25)"),
    current_user: User = Depends(get_current_user),
):
    """
    Cari Facebook Pages berdasarkan keyword menggunakan Graph API search.

    Mengembalikan daftar page dengan: id, nama, kategori, jumlah fans, dan link.
    """
    from app.integrations.facebook.connector import FacebookConnector
    connector = FacebookConnector(settings.facebook_access_token)

    try:
        raw = await connector.search_pages(q.strip(), limit=limit)
        pages = raw.get("data", [])
    except Exception as exc:
        return build_success_response({
            "query":   q,
            "results": [],
            "error":   str(exc),
        })

    results = []
    for p in pages:
        pic = (p.get("picture") or {}).get("data", {})
        results.append({
            "page_id":  p.get("id", ""),
            "name":     p.get("name", ""),
            "category": p.get("category", ""),
            "fans":     p.get("fan_count", 0),
            "link":     p.get("link", ""),
            "picture":  pic.get("url", ""),
        })

    return build_success_response({
        "query":   q,
        "count":   len(results),
        "results": results,
    })
