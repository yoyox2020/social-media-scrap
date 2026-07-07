"""
TikTok API endpoints — Fase 1 (scrape dasar, mirroring Facebook/Instagram).

GET  /tiktok/profile              — profil ringkas (followers/nama) dari akun manapun (Apify, live)
GET  /tiktok/posts?username=X     — scrape (via Apify) + ambil post dari akun manapun
GET  /tiktok/posts/search?q=...   — cari post lokal by keyword/hashtag/rentang tanggal, atau q kosong = tampilkan SEMUA data lokal

BELUM ADA (Fase 2, menyusul): trending/analysis-summary/comments/scrape(background)/
discover/trend-scrape (butuh integrasi trend_recommendations Subsistem A+B dulu).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.users.models import User
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import get_current_user
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
    Cari post TikTok berdasarkan isi CAPTION/HASHTAG/rentang tanggal yang
    sudah tersimpan dari scrape sebelumnya (Fase 1 — HANYA baca database
    lokal, BELUM ada fallback cari topik+scrape otomatis seperti Facebook/
    Instagram, karena integrasi trend_recommendations untuk TikTok belum
    dibangun, menyusul Fase 2).

    - `q` DIISI: cari keyword/hashtag (opsional dipersempit `date_from`/`date_to`).
      Tidak ketemu → `source: "not_found"`, TIDAK ada auto-scrape.
    - `q` KOSONG: tampilkan SEMUA post TikTok lokal (urut published_at
      terbaru dulu), bisa dipersempit `date_from`/`date_to`.

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
    return build_success_response({
        "query": q_clean or None, "date_from": str(date_from) if date_from else None,
        "date_to": str(date_to) if date_to else None,
        "source": "database" if items else "not_found",
        "total": total, "offset": offset, "limit": limit, "items": items,
    })
