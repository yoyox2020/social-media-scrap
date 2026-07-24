"""REST API YouTube Transcript Agent (2026-07-25) -- baca publik
(transcript sudah tersimpan, hasil kerja Metadata Agent + Transcript
Agent), tulis (trigger backfill/config proxy) admin-only."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.youtube.transcript_backfill import backfill_transcripts
from app.domain.posts.models import Post
from app.domain.users.models import User
from app.domain.youtube_transcript.models import YoutubeTranscript, YoutubeTranscriptSegment
from app.infrastructure.database.connection import get_db
from app.services.auth.dependencies import require_admin
from app.services.youtube_transcript import config as cfg
from app.shared.exceptions import NotFoundError
from app.shared.utils import build_success_response

router = APIRouter(prefix="/youtube/transcript", tags=["youtube-transcript"])


@router.get("/statistics", response_model=dict)
async def get_statistics(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(YoutubeTranscript.source, func.count()).group_by(YoutubeTranscript.source)
    )).all()
    counts = {source: count for source, count in rows}
    total_youtube_posts = await db.scalar(select(func.count()).select_from(Post).where(Post.platform == "youtube"))
    return build_success_response({
        "manual": counts.get("manual", 0),
        "generated": counts.get("generated", 0),
        "unavailable": counts.get("unavailable", 0),
        "error": counts.get("error", 0),
        "processed": sum(counts.values()),
        "total_youtube_posts": total_youtube_posts or 0,
    })


@router.get("/{video_id}", response_model=dict)
async def get_transcript(video_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(YoutubeTranscript).where(YoutubeTranscript.video_external_id == video_id))
    if not row:
        raise NotFoundError(f"Transcript utk video '{video_id}' belum diproses (belum di-backfill atau bukan video YouTube)")
    return build_success_response({
        "video_id": row.video_external_id,
        "language": row.language,
        "is_generated": row.is_generated,
        "is_translated": row.is_translated,
        "source": row.source,
        "segment_count": row.segment_count,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat(),
    })


@router.get("/{video_id}/segments", response_model=dict)
async def get_segments(video_id: str, db: AsyncSession = Depends(get_db)):
    transcript = await db.scalar(select(YoutubeTranscript).where(YoutubeTranscript.video_external_id == video_id))
    if not transcript:
        raise NotFoundError(f"Transcript utk video '{video_id}' belum diproses")
    segments = (await db.scalars(
        select(YoutubeTranscriptSegment)
        .where(YoutubeTranscriptSegment.transcript_id == transcript.id)
        .order_by(YoutubeTranscriptSegment.segment_index)
    )).all()
    return build_success_response({
        "video_id": video_id,
        "segment_count": len(segments),
        "segments": [
            {"index": s.segment_index, "start": s.start_second, "end": s.end_second, "duration": s.duration, "text": s.text}
            for s in segments
        ],
    })


@router.post("/backfill", response_model=dict)
async def trigger_backfill(
    limit: int | None = Query(default=None, ge=1, le=500, description="Override batch_size default sekali panggil"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Jalankan Transcript Agent SEKARANG thd video yg belum py transcript
    -- SAMA persis dgn yg jalan otomatis via Celery beat tiap jam."""
    result = await backfill_transcripts(db, limit=limit)
    return build_success_response(result)


@router.get("/config/current", response_model=dict)
async def get_config(_admin: User = Depends(require_admin)):
    creds = await cfg.get_proxy_credentials()
    return build_success_response({
        "proxy_username_masked": cfg.mask_credential(creds[0]) if creds else None,
        "proxy_configured": creds is not None,
        "batch_size": await cfg.get_batch_size(),
    })


@router.patch("/config/current", response_model=dict)
async def update_config(
    proxy_username: str | None = Query(default=None, description="Username proxy Webshare (WAJIB pakai suffix -rotate)"),
    proxy_password: str | None = Query(default=None),
    batch_size: int | None = Query(default=None),
    _admin: User = Depends(require_admin),
):
    if proxy_username and proxy_password:
        await cfg.set_proxy_credentials(proxy_username, proxy_password)
    if batch_size:
        await cfg.set_batch_size(batch_size)
    creds = await cfg.get_proxy_credentials()
    return build_success_response({
        "proxy_username_masked": cfg.mask_credential(creds[0]) if creds else None,
        "proxy_configured": creds is not None,
        "batch_size": await cfg.get_batch_size(),
    })
