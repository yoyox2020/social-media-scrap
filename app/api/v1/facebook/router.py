"""
Facebook API endpoints.

GET  /facebook/profile              — profil ringkas (followers/nama) dari page/profile manapun (Apify, live)
GET  /facebook/posts?username=X     — scrape (via Apify) + ambil post dari page/profil manapun (maks 10)
GET  /facebook/posts/search?q=...   — cari post yang SUDAH discrape berdasarkan keyword (bukan username)
GET  /facebook/trending              — topik trending Facebook dari trend_recommendations
GET  /facebook/analysis/summary      — ringkasan menyeluruh hasil analisis sentimen Facebook
GET  /facebook/comments              — list komentar Facebook dengan filter
POST /facebook/scrape                — trigger scraping Facebook identifier secara background (Celery)
POST /facebook/discover?keyword=...  — cari topik+akun Facebook by keyword LANGSUNG (Apify search, tanpa AI menebak), submit ke trend_recommendations
POST /facebook/trend-scrape/run      — trigger manual batch scrape trend_recommendations
GET  /facebook/trend-scrape/status   — monitoring pipeline scrape trend_recommendations
GET  /facebook/search?q=keyword      — cari PAGE Facebook (Meta Graph API, TERBUKTI mati — lihat docs/flow scrape/flow-scrap-facebook.md)
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

router = APIRouter(prefix="/facebook", tags=["facebook"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/profile
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/profile", response_model=dict, summary="Profil ringkas Facebook dari page/profile (Apify, live)")
async def get_facebook_profile(
    username: str = Query(..., min_length=1, max_length=200, description="Username / slug Facebook (tanpa @)"),
    current_user: User = Depends(get_current_user),
):
    """
    Ambil profil ringkas Facebook (followers, nama, deskripsi) via Apify —
    LIVE lookup langsung ke provider, TIDAK disimpan ke DB (beda dengan
    `GET /facebook/posts` yang men-scrape+simpan post).

    Cuma minta 1 post (paling murah) semata-mata untuk dapat data profil
    yang menyertai tiap baris hasil Apify (`profileFollowers`,
    `profileDescription`, dst).
    """
    identifier = username.strip().lstrip("@")

    from app.services.facebook.providers.registry import search_profile_with_fallback
    from app.shared.exceptions import ExternalAPIError

    try:
        rows, provider_used = await search_profile_with_fallback(identifier, max_posts=1, max_comments=1)
    except ExternalAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if not rows:
        raise HTTPException(status_code=404, detail=f"Tidak ada data untuk @{identifier} (provider: {provider_used})")

    row = rows[0]
    return build_success_response({
        "platform":  "facebook",
        "username":  identifier,
        "provider_used": provider_used,
        "profile": {
            "name":        row.get("profileName", identifier),
            "description": row.get("profileDescription", ""),
            "followers":   row.get("profileFollowers", 0),
            "url":         row.get("profileUrl") or f"https://www.facebook.com/{identifier}",
        },
    })


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
# GET /facebook/trending
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending", response_model=dict, summary="Topik trending Facebook dari trend_recommendations")
async def get_facebook_trending(
    recommendation_date: date | None = Query(default=None, description="Default: hari ini"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ambil topik viral Facebook (dari `trend_recommendations`, diisi AI discovery
    harian atau submit manual via `POST /trend-recommendations`) beserta post +
    sentimen komentar hasil scrape.

    Scraping otomatis berjalan tiap hari (Celery Beat, jadwal di .env), maksimal
    `settings.facebook_trend_daily_budget` topik/hari (urut score tertinggi).
    `status='used'` berarti sudah discrape, `status='pending'` berarti masih
    menunggu giliran.
    """
    from app.domain.trend_recommendations.models import TrendRecommendation

    target_date = recommendation_date or date.today()
    topics = (await db.scalars(
        select(TrendRecommendation)
        .where(TrendRecommendation.recommendation_date == target_date)
        .order_by(TrendRecommendation.score.desc())
    )).all()

    fb_topics = []
    for t in topics:
        fb_account = next(
            (a for a in (t.related_accounts or []) if a.get("platform") == "facebook"),
            None,
        )
        if fb_account:
            fb_topics.append((t, fb_account["username"]))

    if not fb_topics:
        return build_success_response({
            "platform":      "facebook",
            "date":          target_date.isoformat(),
            "total_topics":  0,
            "daily_budget":  settings.facebook_trend_daily_budget,
            "message": "Belum ada topik trending Facebook untuk tanggal ini. Submit via POST /trend-recommendations.",
            "topics": [],
        })

    result_topics = []
    for topic, username in fb_topics:
        rows = (await db.execute(text("""
            SELECT p.id, p.external_id, p.content, p.url, p.published_at, p.metadata
            FROM posts p
            WHERE p.platform = 'facebook' AND p.author = :username
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
                "url":          r["url"] or f"https://www.facebook.com/{r['external_id']}",
                "message":      (r["content"] or "")[:200],
                "likes":        meta.get("likes", 0),
                "comment_count": meta.get("comments", 0),
                "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                "comments":     comments_by_post.get(pid, []),
            })

        result_topics.append({
            "topic":              topic.topic,
            "score":              topic.score,
            "status":             topic.status,
            "facebook_identifier": username,
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
        "platform":       "facebook",
        "date":           target_date.isoformat(),
        "total_topics":   len(result_topics),
        "daily_budget":   settings.facebook_trend_daily_budget,
        "schedule": (
            f"{settings.facebook_trend_scrape_schedule_hour:02d}:"
            f"{settings.facebook_trend_scrape_schedule_minute:02d} WIB otomatis (Celery Beat)"
        ),
        "topics":         result_topics,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/analysis/summary — ringkasan MENYELURUH hasil analisis sentimen
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/analysis/summary", response_model=dict,
            summary="Ringkasan menyeluruh hasil analisis sentimen Facebook (semua akun)")
async def get_facebook_analysis_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ringkasan MENYELURUH hasil scrape + analisis sentimen Facebook, lintas SEMUA
    akun/topik yang pernah discrape — baik dari pipeline `trend_recommendations`
    maupun scrape manual (`POST /facebook/scrape`). Beda dengan `/trending` yang
    cuma menampilkan topik dari `trend_recommendations` per tanggal tertentu.

    - `overall`: total post/komentar/sudah dianalisis + breakdown sentimen keseluruhan
    - `per_account`: breakdown yang sama, dipecah per akun (urut jumlah komentar terbanyak)
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
        WHERE p.platform = 'facebook'
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
        WHERE p.platform = 'facebook'
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
# GET /facebook/comments
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comments", response_model=dict, summary="List komentar Facebook dengan filter")
async def list_facebook_comments(
    username: str | None = Query(default=None, description="Filter per username/page pemilik post"),
    post_id: str | None = Query(default=None, description="Facebook post_id (external_id)"),
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
    List komentar Facebook yang sudah di-scrape.

    Setiap komentar terikat ke satu post spesifik melalui `post_id` (FK).
    Filter tersedia:
    - `username`  — pemilik post/page (bukan author komentar)
    - `post_id`   — Facebook post external_id
    - `post_uuid` — UUID internal post di DB
    - `sentiment` — positif | negatif | netral
    - `date_from` / `date_to` — rentang tanggal scrape komentar

    Response setiap item menyertakan info post induknya (`post_id`, `post_url`, `message`)
    sehingga relasi komentar → post selalu jelas.
    """
    filters = ["p.platform = 'facebook'"]
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
                "message":      (r["caption"] or "")[:200],
                "post_url":     r["post_url"] or f"https://www.facebook.com/{r['post_id']}",
                "published_at": r["post_published_at"].isoformat() if r["post_published_at"] else None,
            },
        })

    return build_success_response({
        "platform": "facebook",
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
# POST /facebook/scrape — trigger scraping identifier via Celery (background)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scrape", response_model=dict, status_code=202,
             summary="Trigger scraping Facebook identifier secara background (Celery)")
async def trigger_facebook_scrape(
    username: str = Query(..., min_length=1, max_length=200, description="Username / Page ID Facebook (tanpa @)"),
    max_posts: int = Query(default=5, ge=1, le=10, description="Maks post per identifier (1-10)"),
    max_comments: int = Query(default=5, ge=0, le=50, description="Maks komentar per post (0-50)"),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger scraping Facebook secara async via Celery worker.

    Berbeda dengan `GET /facebook/posts` yang scrape inline (request harus tunggu
    selesai), endpoint ini langsung return **202 Accepted** dan scraping berjalan
    di background.

    **Gunakan ini untuk:**
    - Scrape identifier baru tanpa memblok response
    - Trigger ulang scrape identifier yang sudah ada
    - Integrasi dengan scheduler / cron eksternal

    **Cek hasil setelah selesai:**
    `GET /facebook/posts?username={username}` — ambil dari DB
    `GET /facebook/comments?username={username}` — list komentar
    """
    from app.workers.facebook_trending_worker import facebook_scrape_identifier_task

    clean_identifier = username.strip().lstrip("@")
    task = facebook_scrape_identifier_task.delay(
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
        "message":      f"Scraping @{clean_identifier} dijadwalkan. Cek hasilnya di GET /facebook/posts?username={clean_identifier}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /facebook/trend-scrape/run — trigger manual batch harian (trend_recommendations)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/trend-scrape/run", response_model=dict, status_code=202,
             summary="Trigger manual batch scrape trend_recommendations (tanpa nunggu jadwal harian)")
async def trigger_facebook_trend_scrape_run(
    current_user: User = Depends(get_current_user),
):
    """
    Trigger manual proses scraping batch harian Facebook dari `trend_recommendations`
    (sama seperti yang jalan otomatis via Celery Beat, jadwal di .env).

    Tetap mengikuti budget `settings.facebook_trend_daily_budget` — topik yang
    sudah `status='used'` tidak di-scrape ulang. Gunakan ini untuk testing atau
    kalau tidak mau menunggu jadwal harian.
    """
    from app.workers.facebook_trending_worker import facebook_trend_recommendation_daily_task

    task = facebook_trend_recommendation_daily_task.delay()

    return build_success_response({
        "status":  "queued",
        "task_id": task.id,
        "message": "Batch scrape trend_recommendations dijadwalkan di background.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# POST /facebook/discover — search Facebook by keyword LANGSUNG (tanpa AI
# menebak akun), submit hasilnya ke trend_recommendations
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/discover", response_model=dict,
             summary="Cari topik+akun Facebook by keyword LANGSUNG (Apify search, tanpa AI menebak), submit ke trend_recommendations")
async def trigger_facebook_discover(
    keyword: str = Query(..., min_length=1, max_length=255, description="Kata kunci/topik yang mau dicari di Facebook"),
    max_results: int = Query(default=10, ge=1, le=20, description="Maks post yang di-scrape dari hasil search (pay-per-result di Apify)"),
    location: str | None = Query(default=None, description="Filter lokasi opsional, mis. 'Indonesia' (diteruskan ke Apify)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Alternatif AI viral discovery KHUSUS Facebook — search Facebook LANGSUNG
    by keyword via Apify (`facebook-search-ppr`), identifier akun diambil dari
    data `author` yang sudah terstruktur di hasil pencarian (BUKAN ditebak
    AI). Beda dengan `POST /facebook/trend-scrape/run` yang cuma menjalankan
    ULANG batch topik yang SUDAH ADA — endpoint ini MENCARI topik BARU.

    - Kalau ketemu akun: langsung submit ke `trend_recommendations`
      (`source='manual_facebook_search'`, `status='pending'`) — topiknya
      lalu ikut antrian budget harian scrape normal (BUKAN langsung
      discrape saat itu juga).
    - Response menyertakan `sample_posts` (5 post pertama + identifier yang
      berhasil diekstrak) supaya kelihatan transparan apa yang sebenarnya
      ditemukan, bukan cuma "berhasil"/"gagal".

    **Biaya**: pay-per-result di Apify (~$0.003/hasil per Juli 2026) — akun
    Apify FREE dibatasi 5 hasil per panggilan.
    """
    from app.services.facebook.trend_scrape_service import discover_facebook_topic_by_keyword

    result = await discover_facebook_topic_by_keyword(db, keyword.strip(), max_results=max_results, location=location)
    return build_success_response(result)


# ─────────────────────────────────────────────────────────────────────────────
# GET /facebook/trend-scrape/status — monitoring pipeline trend_recommendations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trend-scrape/status", response_model=dict,
            summary="Monitoring pipeline scrape trend_recommendations (pending/used, riwayat run)")
async def get_facebook_trend_scrape_status(
    recent_limit: int = Query(default=10, ge=1, le=50, description="Jumlah riwayat scrape_runs terakhir"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Snapshot status pipeline Facebook trend-recommendations, tanpa perlu psql manual:

    - Ringkasan berapa topik `pending` vs `used` yang punya akun Facebook
      (lintas tanggal, bukan cuma hari ini — supaya kelihatan backlog-nya)
    - Daftar topik `pending` yang akan diambil giliran berikutnya (urut score)
    - Riwayat run terakhir dari `scrape_runs` (sukses/gagal, durasi, error)

    Scraping otomatis jalan tiap hari (Celery Beat, jadwal di .env), maksimal
    `settings.facebook_trend_daily_budget` topik/hari. Kalau sebuah topik gagal
    di-scrape (0 post), statusnya TETAP `pending` dan otomatis dicoba lagi di run
    berikutnya — tidak perlu campur tangan manual.
    """
    from app.services.facebook.trend_scrape_service import get_facebook_trend_scrape_summary

    return build_success_response(await get_facebook_trend_scrape_summary(db, recent_limit=recent_limit))


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
