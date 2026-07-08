"""
Twitter/X API endpoints — Fase 1 (scrape dasar, mirroring Facebook/TikTok).

GET /twitter/profile              — profil ringkas dari akun manapun (Apify, live)
GET /twitter/posts?username=X     — scrape (via Apify) + ambil tweet dari akun manapun
GET /twitter/posts/search?q=...   — cari tweet lokal by keyword/hashtag/rentang tanggal
                                     (3 tingkat: DB -> trend_recommendations -> search
                                     langsung), atau q kosong = tampilkan SEMUA data lokal

Fase 2 menyusul: trend_recommendations batch harian (Subsistem B), POST /discover,
GET /trending, /analysis/summary, /comments, POST /scrape (Celery), dashboard.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.utils import build_success_response

router = APIRouter(prefix="/twitter", tags=["twitter"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /twitter/profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile", response_model=dict, summary="Profil ringkas Twitter/X dari akun manapun (Apify, live)")
async def get_twitter_profile(
    username: str = Query(..., min_length=1, max_length=200, description="Username Twitter/X (tanpa @)"),
    current_user: User = Depends(get_current_user),
):
    """
    Ambil profil ringkas Twitter/X (followers, nama) via Apify — LIVE lookup
    langsung ke provider, TIDAK disimpan ke DB (beda dengan `GET /twitter/posts`
    yang men-scrape+simpan tweet).

    Minta 1 tweet saja (paling murah, tidak fetch balasan) semata-mata untuk
    dapat data profil yang menyertai `author` di hasil Apify.
    """
    from app.integrations.apify.twitter import scrape_twitter_via_apify
    from app.shared.exceptions import ExternalAPIError

    identifier = username.strip().lstrip("@")

    try:
        rows = await scrape_twitter_via_apify(identifier, max_posts=1, max_comments=0)
    except ExternalAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not rows:
        raise HTTPException(status_code=404, detail=f"Tidak ada data untuk @{identifier}")

    author = rows[0].get("author") or {}
    return build_success_response({
        "platform":  "twitter",
        "username":  identifier,
        "provider_used": "apify",
        "profile": {
            "name":        author.get("name", identifier),
            "screen_name": author.get("screen_name", identifier),
            "followers":   author.get("followers_count", 0),
            "verified":    author.get("blue_verified", False),
            "url":         f"https://x.com/{identifier}",
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /twitter/posts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts", response_model=dict, summary="Scrape + ambil tweet dari akun manapun")
async def get_twitter_posts(
    username: str = Query(..., min_length=1, max_length=200, description="Username Twitter/X"),
    max_posts: int = Query(default=10, ge=1, le=20, description="Jumlah tweet (maks 20)"),
    max_comments: int = Query(default=10, ge=0, le=20, description="Jumlah balasan per tweet (maks 20)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape tweet dari akun Twitter/X manapun via Apify (`danek/twitter-scraper`),
    simpan ke DB, analisis sentimen post (IndoBERT) + balasan (lexicon).

    - Jika data sudah ada dan `force_refresh=false` → return dari DB
    - Jika belum ada atau `force_refresh=true` → scrape via Apify
    - Dedup akun-per-hari otomatis (skip panggil Apify kalau sudah discrape hari ini)

    **Catatan biaya**: balasan tiap tweet butuh 1 actor call TERPISAH (lihat
    app/integrations/apify/twitter.py) — lebih mahal per unit dibanding
    Facebook/TikTok, `max_comments` dibatasi lebih rendah (maks 20).
    """
    identifier = username.strip().lstrip("@")

    existing_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM posts WHERE platform = 'twitter' AND author = :author"),
        {"author": identifier},
    ) or 0

    scrape_result: dict | None = None
    if existing_count == 0 or force_refresh:
        from app.services.twitter.pipeline_service import scrape_twitter_posts_via_provider
        scrape_result = await scrape_twitter_posts_via_provider(
            db=db, identifier=identifier, max_posts=max_posts, max_comments=max_comments, keyword_id=None,
        )

    rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'twitter' AND author = :author
        ORDER BY published_at DESC NULLS LAST
        LIMIT :limit
    """), {"author": identifier, "limit": max_posts})).mappings().all()

    page_info: dict = {"username": identifier}
    if rows and (rows[0]["metadata"] or {}).get("followers"):
        page_info["followers"] = rows[0]["metadata"]["followers"]

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
            "tweet_id":      r["external_id"],
            "url":           r["url"] or f"https://x.com/{identifier}/status/{r['external_id']}",
            "text":          r["content"] or "",
            "author":        r["author"],
            "likes":         meta.get("likes", 0),
            "retweets":      meta.get("retweets", 0),
            "quotes":        meta.get("quotes", 0),
            "views":         meta.get("views", 0),
            "comment_count": total_per_post.get(pid, meta.get("comments", 0)),
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
        "platform":  "twitter",
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
# GET /twitter/posts/search — keyword/hashtag/rentang tanggal, atau tampilkan semua data lokal
# ─────────────────────────────────────────────────────────────────────────────

async def _build_twitter_search_items(db: AsyncSession, post_rows) -> list[dict]:
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
            "text":           r["content"],
            "url":            r["url"],
            "likes":          meta.get("likes", 0),
            "retweets":       meta.get("retweets", 0),
            "views":          meta.get("views", 0),
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
            summary="Cari tweet (keyword/hashtag/rentang tanggal) atau tampilkan SEMUA data lokal")
async def search_twitter_posts(
    q: str | None = Query(default=None, min_length=1, max_length=200, description="Keyword atau hashtag (boleh pakai # atau tidak). KOSONGKAN untuk tampilkan semua tweet lokal."),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (published_at)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (published_at)"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari tweet — 3 tingkat kalau `q` diisi (mirroring Facebook/TikTok):
    1. Database lokal (posts.content/hashtag).
    2. Tidak ketemu → cari topik cocok di `trend_recommendations` (PALING
       BARU dulu, bukan score), scrape akunnya SEKARANG kalau ketemu.
    3. Topik juga tidak ketemu (keyword genuinely baru) → search LANGSUNG
       ke Twitter/X via Apify, scrape akun yang ketemu SEKARANG JUGA, submit
       ke trend_recommendations sekalian.

    - `q` KOSONG: tampilkan SEMUA tweet lokal (urut published_at terbaru
      dulu), bisa dipersempit `date_from`/`date_to`. TIDAK ada fallback apa pun.

    Pagination via `limit`/`offset`, `total` = jumlah row sebenarnya (bukan
    cuma count di halaman ini).
    """
    q_clean = (q or "").strip().lstrip("#")

    filters = ["p.platform = 'twitter'"]
    params: dict = {"limit": limit, "offset": offset}

    if q_clean:
        filters.append("(p.content ILIKE :kw OR e.text ILIKE :kw_exact)")
        params["kw"] = f"%{q_clean}%"
        params["kw_exact"] = q_clean
    if date_from:
        filters.append("p.published_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        filters.append("p.published_at <= :date_to")
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)

    where_clause = " AND ".join(filters)
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    total: int = (await db.scalar(text(f"""
        SELECT COUNT(DISTINCT p.id) FROM posts p
        LEFT JOIN entities e ON e.post_id = p.id AND e.entity_type = 'HASHTAG'
        WHERE {where_clause}
    """), count_params)) or 0

    post_rows = (await db.execute(text(f"""
        SELECT DISTINCT p.id, p.external_id, p.content, p.author, p.url,
               p.published_at, p.metadata
        FROM posts p
        LEFT JOIN entities e ON e.post_id = p.id AND e.entity_type = 'HASHTAG'
        WHERE {where_clause}
        ORDER BY p.published_at DESC NULLS LAST
        OFFSET :offset LIMIT :limit
    """), params)).mappings().all()

    items = await _build_twitter_search_items(db, post_rows)
    if items or not q_clean:
        return build_success_response({
            "query": q_clean or None, "date_from": str(date_from) if date_from else None,
            "date_to": str(date_to) if date_to else None,
            "source": "database" if items else "not_found",
            "total": total, "offset": offset, "limit": limit, "items": items,
        })

    # ── Tingkat 2: q diisi tapi tidak ketemu -> cari topik cocok di
    # trend_recommendations, PALING BARU dulu ──────────────────────────────────
    from app.domain.trend_recommendations.models import TrendRecommendation

    candidate_topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.topic.ilike(f"%{q_clean}%"))
        .order_by(TrendRecommendation.recommendation_date.desc(), TrendRecommendation.score.desc())
    )).all()

    matched_topic = None
    matched_identifier = None
    for t in candidate_topics:
        for acc in t.related_accounts or []:
            if acc.get("platform") == "twitter" and acc.get("username"):
                matched_topic = t
                matched_identifier = acc["username"]
                break
        if matched_topic:
            break

    if matched_topic:
        return await _scrape_now_and_respond(
            db, q_clean, matched_identifier, limit,
            topic=matched_topic.topic, mark_topic=matched_topic,
        )

    # ── Tingkat 3: topik juga tidak ketemu (keyword genuinely baru) -> search
    # LANGSUNG ke Twitter/X via Apify ────────────────────────────────────────
    from app.services.twitter.trend_scrape_service import discover_twitter_topic_by_keyword

    discover_result = await discover_twitter_topic_by_keyword(db, q_clean, max_results=5)
    accounts_found = discover_result.get("accounts_found") or []

    if not accounts_found:
        return build_success_response({
            "query": q_clean, "source": "not_found", "total": 0, "items": [],
            "message": (
                "Tidak ditemukan tweet di database, topik terkait di trend_recommendations, "
                "MAUPUN akun Twitter/X nyata yang bahas keyword ini via search langsung."
            ),
        })

    new_identifier = accounts_found[0]["username"]
    return await _scrape_now_and_respond(
        db, q_clean, new_identifier, limit,
        topic=q_clean, mark_topic=None, source_label="scraped_now_external",
    )


async def _scrape_now_and_respond(
    db: AsyncSession, q_clean: str, identifier: str, limit: int,
    topic: str, mark_topic, source_label: str = "scraped_now",
) -> dict:
    """Scrape 1 identifier SEKARANG via Apify, tandai topik 'used' (kalau ada),
    return tweet-tweet barunya. Dipakai tingkat 2 & 3 di search_twitter_posts()."""
    from app.domain.scrape_runs.models import ScrapeRun
    from app.services.twitter.pipeline_service import scrape_twitter_posts_via_provider

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text=f"search:{identifier}", platform="twitter", api_source="apify",
        status="running", triggered_by="manual_api", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

    scrape_result = await scrape_twitter_posts_via_provider(
        db=db, identifier=identifier, max_posts=5, max_comments=5, keyword_id=None,
    )

    scrape_run.status = "success" if scrape_result.get("posts_scraped", 0) > 0 else "failed"
    scrape_run.api_source = scrape_result.get("provider_used") or "apify"
    scrape_run.videos_fetched = scrape_result.get("posts_scraped", 0)
    scrape_run.videos_new = scrape_result.get("posts_saved", 0)
    scrape_run.error_message = "; ".join(scrape_result.get("errors", [])[:3]) or None
    scrape_run.finished_at = datetime.now(timezone.utc)
    scrape_run.duration_seconds = (scrape_run.finished_at - started_at).total_seconds()

    if scrape_run.status == "success" and mark_topic is not None:
        mark_topic.status = "used"

    await db.commit()

    fresh_rows = (await db.execute(text("""
        SELECT p.id, p.external_id, p.content, p.author, p.url, p.published_at, p.metadata
        FROM posts p
        WHERE p.platform = 'twitter' AND p.author = :identifier
        ORDER BY p.published_at DESC NULLS LAST
        LIMIT :limit
    """), {"identifier": identifier, "limit": limit})).mappings().all()

    items = await _build_twitter_search_items(db, fresh_rows)
    return build_success_response({
        "query": q_clean, "source": source_label, "total": len(items),
        "topic": topic, "twitter_identifier": identifier,
        "note": "Sentimen post baru diproses async (Celery) — mungkin belum muncul kalau baru saja discrape.",
        "items": items,
    })
