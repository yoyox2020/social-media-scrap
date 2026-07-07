"""
TikTok API endpoints — Fase 1+2 (scrape dasar + trend_recommendations,
mirroring Facebook/Instagram).

GET  /tiktok/profile              — profil ringkas (followers/nama) dari akun manapun (Apify, live)
GET  /tiktok/posts?username=X     — scrape (via Apify) + ambil post dari akun manapun
GET  /tiktok/posts/search?q=...   — cari post lokal by keyword/hashtag/rentang tanggal, atau q kosong = tampilkan SEMUA data lokal
GET  /tiktok/trending              — topik trending TikTok dari trend_recommendations
GET  /tiktok/analysis/summary      — ringkasan menyeluruh hasil analisis sentimen TikTok
GET  /tiktok/comments              — list komentar TikTok dengan filter
POST /tiktok/scrape                — trigger scraping TikTok identifier secara background (Celery)
POST /tiktok/discover?keyword=...  — cari topik+akun TikTok by keyword LANGSUNG (Apify search, tanpa AI menebak), submit ke trend_recommendations
POST /tiktok/trend-scrape/run      — trigger manual batch scrape trend_recommendations
GET  /tiktok/trend-scrape/status   — monitoring pipeline scrape trend_recommendations
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
from app.shared.config import settings
from app.shared.utils import build_success_response

router = APIRouter(prefix="/tiktok", tags=["tiktok"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile", response_model=dict, summary="Profil ringkas TikTok dari akun manapun (Apify, live)")
async def get_tiktok_profile(
    username: str = Query(..., min_length=1, max_length=200, description="Username TikTok (tanpa @)"),
    current_user: User = Depends(get_current_user),
):
    """
    Ambil profil ringkas TikTok (followers, nama) via Apify — LIVE lookup
    langsung ke provider, TIDAK disimpan ke DB (beda dengan `GET /tiktok/posts`
    yang men-scrape+simpan post).

    Cuma minta 1 post (paling murah) semata-mata untuk dapat data profil yang
    menyertai `authorMeta` di hasil Apify.
    """
    from app.integrations.apify.tiktok import scrape_tiktok_via_apify
    from app.shared.exceptions import ExternalAPIError

    identifier = username.strip().lstrip("@")

    try:
        rows = await scrape_tiktok_via_apify(identifier, max_posts=1, max_comments=0)
    except ExternalAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not rows:
        raise HTTPException(status_code=404, detail=f"Tidak ada data untuk @{identifier}")

    author = rows[0].get("authorMeta") or {}
    return build_success_response({
        "platform":  "tiktok",
        "username":  identifier,
        "provider_used": "apify",
        "profile": {
            "name":        author.get("nickName") or author.get("name", identifier),
            "followers":   author.get("fans", 0),
            "following":   author.get("following", 0),
            "hearts":      author.get("heart", 0),
            "video_count": author.get("video", 0),
            "url":         author.get("profileUrl") or f"https://www.tiktok.com/@{identifier}",
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/posts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts", response_model=dict, summary="Scrape + ambil post TikTok dari akun manapun")
async def get_tiktok_posts(
    username: str = Query(..., min_length=1, max_length=200, description="Username TikTok"),
    max_posts: int = Query(default=10, ge=1, le=20, description="Jumlah post (maks 20)"),
    max_comments: int = Query(default=10, ge=0, le=30, description="Jumlah komentar per post (maks 30)"),
    force_refresh: bool = Query(default=False, description="Paksa scrape ulang"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape post dari akun TikTok manapun via Apify
    (`clockworks/tiktok-scraper`), simpan ke DB, analisis sentimen post
    (IndoBERT) + komentar (lexicon).

    - Jika data sudah ada dan `force_refresh=false` → return dari DB
    - Jika belum ada atau `force_refresh=true` → scrape via Apify
    - Dedup akun-per-hari otomatis (skip panggil Apify kalau sudah discrape hari ini)

    **Catatan keterbatasan data**: komentar TikTok dari actor ini tidak
    menyertakan nama tampilan komentator, cuma ID numerik (`uniqueId`/`uid`).
    """
    identifier = username.strip().lstrip("@")

    existing_count: int = await db.scalar(
        text("SELECT COUNT(*) FROM posts WHERE platform = 'tiktok' AND author = :author"),
        {"author": identifier},
    ) or 0

    scrape_result: dict | None = None
    if existing_count == 0 or force_refresh:
        from app.services.tiktok.pipeline_service import scrape_tiktok_posts_via_provider
        scrape_result = await scrape_tiktok_posts_via_provider(
            db=db, identifier=identifier, max_posts=max_posts, max_comments=max_comments, keyword_id=None,
        )

    rows = (await db.execute(text("""
        SELECT id, external_id, content, author, url, published_at, collected_at, metadata
        FROM posts
        WHERE platform = 'tiktok' AND author = :author
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
            "post_id":       r["external_id"],
            "url":           r["url"] or f"https://www.tiktok.com/@{identifier}/video/{r['external_id']}",
            "caption":       r["content"] or "",
            "author":        r["author"],
            "likes":         meta.get("likes", 0),
            "shares":        meta.get("shares", 0),
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
        "platform":  "tiktok",
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
# GET /tiktok/posts/search — keyword/hashtag/rentang tanggal, atau tampilkan semua data lokal
# ─────────────────────────────────────────────────────────────────────────────

async def _build_tiktok_search_items(db: AsyncSession, post_rows) -> list[dict]:
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
            "caption":        r["content"],
            "url":            r["url"],
            "likes":          meta.get("likes", 0),
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
            summary="Cari post TikTok (keyword/hashtag/rentang tanggal) atau tampilkan SEMUA data lokal")
async def search_tiktok_posts(
    q: str | None = Query(default=None, min_length=1, max_length=200, description="Keyword atau hashtag (boleh pakai # atau tidak). KOSONGKAN untuk tampilkan semua post TikTok lokal."),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (published_at)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (published_at)"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cari post TikTok — 3 tingkat kalau `q` diisi (mirroring Facebook):
    1. Database lokal (posts.content/hashtag).
    2. Tidak ketemu → cari topik cocok di `trend_recommendations` (PALING
       BARU dulu, bukan score), scrape akunnya SEKARANG kalau ketemu.
    3. Topik juga tidak ketemu (keyword genuinely baru) → search LANGSUNG
       ke TikTok via Apify (actor sama dengan POST /discover), scrape akun
       yang ketemu SEKARANG JUGA, submit ke trend_recommendations sekalian.

    - `q` KOSONG: tampilkan SEMUA post TikTok lokal (urut published_at
      terbaru dulu), bisa dipersempit `date_from`/`date_to`. TIDAK ada
      fallback apa pun (tidak ada keyword untuk dicocokkan).

    Pagination via `limit`/`offset`, `total` = jumlah row sebenarnya (bukan
    cuma count di halaman ini).
    """
    q_clean = (q or "").strip().lstrip("#")

    filters = ["p.platform = 'tiktok'"]
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

    items = await _build_tiktok_search_items(db, post_rows)
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
            if acc.get("platform") == "tiktok" and acc.get("username"):
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
    # LANGSUNG ke TikTok via Apify (actor sama dengan POST /discover) ──────────
    from app.services.tiktok.trend_scrape_service import discover_tiktok_topic_by_keyword

    discover_result = await discover_tiktok_topic_by_keyword(db, q_clean, max_results=5)
    accounts_found = discover_result.get("accounts_found") or []

    if not accounts_found:
        return build_success_response({
            "query": q_clean, "source": "not_found", "total": 0, "items": [],
            "message": (
                "Tidak ditemukan post di database, topik terkait di trend_recommendations, "
                "MAUPUN akun TikTok nyata yang bahas keyword ini via search langsung."
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
    return post-post barunya. Dipakai tingkat 2 & 3 di search_tiktok_posts()."""
    from app.domain.scrape_runs.models import ScrapeRun
    from app.services.tiktok.pipeline_service import scrape_tiktok_posts_via_provider

    started_at = datetime.now(timezone.utc)
    scrape_run = ScrapeRun(
        keyword_text=f"search:{identifier}", platform="tiktok", api_source="apify",
        status="running", triggered_by="manual_api", started_at=started_at,
    )
    db.add(scrape_run)
    await db.commit()  # commit status='running' segera supaya kelihatan di monitor live

    scrape_result = await scrape_tiktok_posts_via_provider(
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
        WHERE p.platform = 'tiktok' AND p.author = :identifier
        ORDER BY p.published_at DESC NULLS LAST
        LIMIT :limit
    """), {"identifier": identifier, "limit": limit})).mappings().all()

    items = await _build_tiktok_search_items(db, fresh_rows)
    return build_success_response({
        "query": q_clean, "source": source_label, "total": len(items),
        "topic": topic, "tiktok_identifier": identifier,
        "note": "Sentimen post baru diproses async (Celery) — mungkin belum muncul kalau baru saja discrape.",
        "items": items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/trending
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending", response_model=dict, summary="Topik trending TikTok dari trend_recommendations")
async def get_tiktok_trending(
    recommendation_date: date | None = Query(default=None, description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil topik viral TikTok (dari `trend_recommendations`, diisi AI discovery
    harian atau submit manual via `POST /tiktok/discover`) beserta post +
    sentimen komentar hasil scrape.
    """
    from app.domain.trend_recommendations.models import TrendRecommendation

    target_date = recommendation_date or date.today()
    topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.recommendation_date == target_date)
        .order_by(TrendRecommendation.score.desc())
    )).all()

    tt_topics = []
    for t in topics:
        tt_account = next(
            (a for a in (t.related_accounts or []) if a.get("platform") == "tiktok"),
            None,
        )
        if tt_account:
            tt_topics.append((t, tt_account["username"]))

    if not tt_topics:
        return build_success_response({
            "platform":      "tiktok",
            "date":          target_date.isoformat(),
            "total_topics":  0,
            "daily_budget":  settings.tiktok_trend_daily_budget,
            "message": "Belum ada topik trending TikTok untuk tanggal ini. Submit via POST /tiktok/discover.",
            "topics": [],
        })

    result_topics = []
    for topic, username in tt_topics:
        rows = (await db.execute(text("""
            SELECT p.id, p.external_id, p.content, p.url, p.published_at, p.metadata
            FROM posts p
            WHERE p.platform = 'tiktok' AND p.author = :username
            ORDER BY p.published_at DESC NULLS LAST
            LIMIT 2
        """), {"username": username})).mappings().all()

        post_ids = [str(r["id"]) for r in rows]
        comments_by_post: dict[str, list] = {pid: [] for pid in post_ids}
        all_labels: list[str] = []

        if post_ids:
            ids_sql = ", ".join(f"'{pid}'" for pid in post_ids)
            cmt_rows = (await db.execute(text(f"""
                SELECT c.id, c.external_id, c.content, c.author, c.post_id::text AS post_id,
                       la.label AS sentiment, la.score
                FROM comments c
                LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
                WHERE c.post_id::text IN ({ids_sql})
                ORDER BY la.score DESC NULLS LAST
                LIMIT 25
            """))).mappings().all()

            for cr in cmt_rows:
                pid = cr["post_id"]
                if cr["sentiment"]:
                    all_labels.append(cr["sentiment"])
                bucket = comments_by_post.setdefault(pid, [])
                if len(bucket) < 5:
                    bucket.append({
                        "id":         str(cr["id"]),
                        "comment_id": cr["external_id"],
                        "content":    cr["content"],
                        "author":     cr["author"],
                        "sentiment":  cr["sentiment"],
                        "score":      round(float(cr["score"]), 3) if cr["score"] is not None else None,
                    })

        counter = Counter(all_labels)
        total_analyzed = sum(counter.values())

        posts_out = []
        for r in rows:
            pid = str(r["id"])
            meta = r["metadata"] or {}
            posts_out.append({
                "post_id":      r["external_id"],
                "url":          r["url"] or f"https://www.tiktok.com/@{username}/video/{r['external_id']}",
                "caption":      (r["content"] or "")[:200],
                "likes":        meta.get("likes", 0),
                "views":        meta.get("views", 0),
                "comment_count": meta.get("comments", 0),
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                "comments":     comments_by_post.get(pid, []),
            })

        result_topics.append({
            "topic":             topic.topic,
            "score":             topic.score,
            "status":            topic.status,
            "tiktok_identifier": username,
            "sentiment": {
                lbl: {
                    "count":      counter.get(lbl, 0),
                    "percentage": round(counter.get(lbl, 0) / total_analyzed * 100, 1) if total_analyzed else 0.0,
                }
                for lbl in ["positif", "negatif", "netral"]
            },
            "posts": posts_out,
        })

    return build_success_response({
        "platform":       "tiktok",
        "date":           target_date.isoformat(),
        "total_topics":   len(result_topics),
        "daily_budget":   settings.tiktok_trend_daily_budget,
        "schedule": (
            f"{settings.tiktok_trend_scrape_schedule_hour:02d}:"
            f"{settings.tiktok_trend_scrape_schedule_minute:02d} WIB otomatis (Celery Beat)"
        ),
        "topics":         result_topics,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/analysis/summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analysis/summary", response_model=dict,
            summary="Ringkasan menyeluruh hasil analisis sentimen TikTok (semua akun)")
async def get_tiktok_analysis_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ringkasan MENYELURUH hasil scrape + analisis sentimen TikTok, lintas SEMUA
    akun/topik yang pernah discrape — baik dari pipeline `trend_recommendations`
    maupun scrape manual (`POST /tiktok/scrape`).
    """
    overall_row = (await db.execute(text("""
        SELECT
            count(DISTINCT p.id) AS total_posts,
            count(c.id)          AS total_comments,
            count(la.id)         AS total_analyzed,
            count(*) FILTER (WHERE la.label = 'positif') AS positif,
            count(*) FILTER (WHERE la.label = 'negatif') AS negatif,
            count(*) FILTER (WHERE la.label = 'netral')  AS netral
        FROM posts p
        LEFT JOIN comments c ON c.post_id = p.id
        LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
        WHERE p.platform = 'tiktok'
    """))).mappings().first()

    per_account_rows = (await db.execute(text("""
        SELECT
            p.author AS username,
            count(DISTINCT p.id) AS post_count,
            count(c.id)          AS comment_count,
            count(la.id)         AS analyzed_count,
            count(*) FILTER (WHERE la.label = 'positif') AS positif,
            count(*) FILTER (WHERE la.label = 'negatif') AS negatif,
            count(*) FILTER (WHERE la.label = 'netral')  AS netral
        FROM posts p
        LEFT JOIN comments c ON c.post_id = p.id
        LEFT JOIN lexicon_analyses la ON la.comment_id = c.id
        WHERE p.platform = 'tiktok'
        GROUP BY p.author
        ORDER BY comment_count DESC
    """))).mappings().all()

    def _pct(count: int, total: int) -> float:
        return round(count / total * 100, 1) if total else 0.0

    total_comments = overall_row["total_comments"] or 0

    return build_success_response({
        "overall": {
            "total_posts":    overall_row["total_posts"],
            "total_comments": total_comments,
            "total_analyzed": overall_row["total_analyzed"],
            "fully_analyzed": overall_row["total_analyzed"] == total_comments,
            "sentiment": {
                "positif": {"count": overall_row["positif"], "percentage": _pct(overall_row["positif"], total_comments)},
                "negatif": {"count": overall_row["negatif"], "percentage": _pct(overall_row["negatif"], total_comments)},
                "netral":  {"count": overall_row["netral"],  "percentage": _pct(overall_row["netral"], total_comments)},
            },
        },
        "per_account": [
            {
                "username":       r["username"],
                "post_count":     r["post_count"],
                "comment_count":  r["comment_count"],
                "analyzed_count": r["analyzed_count"],
                "fully_analyzed": r["analyzed_count"] == r["comment_count"],
                "sentiment": {
                    "positif": r["positif"],
                    "negatif": r["negatif"],
                    "netral":  r["netral"],
                },
            }
            for r in per_account_rows
        ],
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/comments
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comments", response_model=dict, summary="List komentar TikTok dengan filter")
async def list_tiktok_comments(
    username: str | None = Query(default=None, description="Filter per username pemilik post"),
    post_id: str | None = Query(default=None, description="TikTok video id (external_id)"),
    post_uuid: uuid.UUID | None = Query(default=None, description="UUID internal post di DB"),
    sentiment: str | None = Query(default=None, description="positif | negatif | netral"),
    date_from: date | None = Query(default=None, description="Filter dari tanggal (created_at)"),
    date_to: date | None = Query(default=None, description="Filter sampai tanggal (created_at)"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List komentar TikTok yang sudah di-scrape. **Catatan**: TikTok tidak
    kasih nama tampilan komentator (cuma ID numerik uniqueId/uid) —
    keterbatasan data provider, bukan bug.
    """
    filters = ["p.platform = 'tiktok'"]
    params: dict = {"limit": limit, "offset": offset}

    if username:
        filters.append("p.author = :username")
        params["username"] = username.strip().lstrip("@").lower()
    if post_uuid:
        filters.append("c.post_id = :post_uuid")
        params["post_uuid"] = str(post_uuid)
    elif post_id:
        filters.append("p.external_id = :post_ext_id")
        params["post_ext_id"] = post_id.strip()
    if sentiment:
        filters.append("la.label = :sentiment")
        params["sentiment"] = sentiment
    if date_from:
        filters.append("c.created_at >= :date_from")
        params["date_from"] = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    if date_to:
        filters.append("c.created_at <= :date_to")
        params["date_to"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)

    where_clause = " AND ".join(filters)
    join_type = "JOIN" if sentiment else "LEFT JOIN"

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total: int = (await db.scalar(text(f"""
        SELECT COUNT(*)
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
    """), count_params)) or 0

    rows = (await db.execute(text(f"""
        SELECT
            c.id,
            c.external_id          AS comment_id,
            c.content,
            c.author               AS comment_author,
            c.created_at           AS scraped_at,
            c.metadata,
            p.id                   AS post_uuid,
            p.external_id          AS post_id,
            p.author               AS post_owner,
            p.content              AS caption,
            p.url                  AS post_url,
            p.published_at         AS post_published_at,
            la.label               AS sentiment,
            la.score               AS sentiment_score
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        {join_type} lexicon_analyses la ON la.comment_id = c.id
        WHERE {where_clause}
        ORDER BY c.created_at DESC
        OFFSET :offset LIMIT :limit
    """), params)).mappings().all()

    items = []
    for r in rows:
        meta = r["metadata"] or {}
        items.append({
            "id":             str(r["id"]),
            "comment_id":     r["comment_id"],
            "content":        r["content"],
            "author":         r["comment_author"],
            "sentiment":      r["sentiment"],
            "sentiment_score": round(float(r["sentiment_score"]), 3) if r["sentiment_score"] is not None else None,
            "like_count":     meta.get("like_count", 0),
            "scraped_at":     r["scraped_at"].isoformat() if r["scraped_at"] else None,
            "post": {
                "post_uuid":    str(r["post_uuid"]),
                "post_id":      r["post_id"],
                "post_owner":   r["post_owner"],
                "caption":      (r["caption"] or "")[:200],
                "post_url":     r["post_url"] or f"https://www.tiktok.com/@{r['post_owner']}/video/{r['post_id']}",
                "published_at": r["post_published_at"].isoformat() if r["post_published_at"] else None,
            },
        })

    return build_success_response({
        "platform": "tiktok",
        "filter": {
            "username":  username,
            "post_id":   post_id,
            "post_uuid": str(post_uuid) if post_uuid else None,
            "sentiment": sentiment,
            "date_from": str(date_from) if date_from else None,
            "date_to":   str(date_to) if date_to else None,
        },
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "items":  items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /tiktok/scrape — trigger scraping identifier via Celery (background)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scrape", response_model=dict, status_code=202,
             summary="Trigger scraping TikTok identifier secara background (Celery)")
async def trigger_tiktok_scrape(
    username: str = Query(..., min_length=1, max_length=200, description="Username TikTok (tanpa @)"),
    max_posts: int = Query(default=5, ge=1, le=20, description="Maks post per identifier"),
    max_comments: int = Query(default=5, ge=0, le=30, description="Maks komentar per post"),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger scraping TikTok secara async via Celery worker. Beda dengan
    `GET /tiktok/posts` yang scrape inline, endpoint ini langsung return
    **202 Accepted** dan scraping berjalan di background.
    """
    from app.workers.tiktok_trending_worker import tiktok_scrape_identifier_task

    clean_identifier = username.strip().lstrip("@")
    task = tiktok_scrape_identifier_task.delay(
        identifier=clean_identifier,
        max_posts=max_posts,
        max_comments=max_comments,
    )

    return build_success_response({
        "status":       "queued",
        "task_id":      task.id,
        "username":     clean_identifier,
        "max_posts":    max_posts,
        "max_comments": max_comments,
        "message":      f"Scraping @{clean_identifier} dijadwalkan. Cek hasilnya di GET /tiktok/posts?username={clean_identifier}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /tiktok/discover — search TikTok by keyword LANGSUNG (tanpa AI menebak)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/discover", response_model=dict,
             summary="Cari topik+akun TikTok by keyword LANGSUNG (Apify search, tanpa AI menebak), submit ke trend_recommendations")
async def trigger_tiktok_discover(
    keyword: str = Query(..., min_length=1, max_length=255, description="Kata kunci/topik yang mau dicari di TikTok"),
    max_results: int = Query(default=10, ge=1, le=20, description="Maks post yang di-scrape dari hasil search (pay-per-result di Apify)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Alternatif AI viral discovery KHUSUS TikTok — search TikTok LANGSUNG by
    keyword via Apify (actor yang SAMA dengan scrape profil), identifier akun
    diambil dari `authorMeta.name` yang sudah terstruktur (BUKAN ditebak AI,
    dan TIDAK perlu extract dari URL seperti Facebook — TikTok lebih akurat).

    Kalau ketemu akun: submit ke `trend_recommendations`
    (`source='manual_tiktok_search'`, `status='pending'`) — ikut antrian
    budget harian scrape normal (BUKAN langsung discrape saat itu juga).
    """
    from app.services.tiktok.trend_scrape_service import discover_tiktok_topic_by_keyword

    result = await discover_tiktok_topic_by_keyword(db, keyword.strip(), max_results=max_results)
    return build_success_response(result)


# ─────────────────────────────────────────────────────────────────────────────
# POST /tiktok/trend-scrape/run
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/trend-scrape/run", response_model=dict, status_code=202,
             summary="Trigger manual batch scrape trend_recommendations (tanpa nunggu jadwal harian)")
async def trigger_tiktok_trend_scrape_run(
    current_user: User = Depends(get_current_user),
):
    """
    Trigger manual proses scraping batch harian TikTok dari
    `trend_recommendations` (sama seperti yang jalan otomatis via Celery Beat).
    """
    from app.workers.tiktok_trending_worker import tiktok_trend_recommendation_daily_task

    task = tiktok_trend_recommendation_daily_task.delay()

    return build_success_response({
        "status":  "queued",
        "task_id": task.id,
        "message": "Batch scrape trend_recommendations dijadwalkan di background.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /tiktok/trend-scrape/status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trend-scrape/status", response_model=dict,
            summary="Monitoring pipeline scrape trend_recommendations (pending/used, riwayat run)")
async def get_tiktok_trend_scrape_status(
    recent_limit: int = Query(default=10, ge=1, le=50, description="Jumlah riwayat scrape_runs terakhir"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Snapshot status pipeline TikTok trend-recommendations."""
    from app.services.tiktok.trend_scrape_service import get_tiktok_trend_scrape_summary

    return build_success_response(await get_tiktok_trend_scrape_summary(db, recent_limit=recent_limit))
