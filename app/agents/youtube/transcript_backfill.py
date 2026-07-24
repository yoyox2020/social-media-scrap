"""Agent backfill transcript YouTube (2026-07-25) -- cari video YANG
BELUM py baris `youtube_transcripts`, ambil caption via
app/services/youtube_transcript/fetch.py (proxy Webshare WAJIB, lihat
docstring di situ), simpan per-segment (BUKAN 1 text panjang, permintaan
eksplisit user). Video tanpa caption sama sekali ditandai
source='unavailable' -- TIDAK diproses ulang tiap run (bukan Whisper
fallback, keputusan eksplisit user skip speech-to-text)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.posts.models import Post
from app.domain.youtube_transcript.models import YoutubeTranscript, YoutubeTranscriptSegment
from app.services.youtube_transcript import config as cfg
from app.services.youtube_transcript.fetch import fetch_transcript


async def _get_videos_without_transcript(db: AsyncSession, limit: int) -> list[Post]:
    subq = select(YoutubeTranscript.post_id)
    stmt = (
        select(Post)
        .where(Post.platform == "youtube", Post.id.not_in(subq))
        .order_by(Post.collected_at.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def backfill_transcripts(db: AsyncSession, limit: int | None = None) -> dict:
    creds = await cfg.get_proxy_credentials()
    if not creds:
        return {"error": "Kredensial proxy Webshare belum diatur (lihat PATCH /youtube/transcript/config)", "checked": 0}
    proxy_username, proxy_password = creds

    batch_limit = limit or await cfg.get_batch_size()
    posts = await _get_videos_without_transcript(db, batch_limit)
    if not posts:
        return {"checked": 0, "manual": 0, "generated": 0, "unavailable": 0, "error": 0}

    counts = {"manual": 0, "generated": 0, "unavailable": 0, "error": 0}
    now = datetime.now(timezone.utc)

    for post in posts:
        result = await fetch_transcript(post.external_id, proxy_username, proxy_password)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

        transcript_row = YoutubeTranscript(
            id=uuid.uuid4(), post_id=post.id, video_external_id=post.external_id,
            language=result.get("language_code"),
            is_generated=result.get("is_generated", False),
            is_translated=result.get("is_translated", False),
            source=status,
            segment_count=len(result.get("segments", [])),
            error_message=result.get("error_message"),
            created_at=now, updated_at=now,
        )
        db.add(transcript_row)
        await db.flush()

        for i, seg in enumerate(result.get("segments", [])):
            db.add(YoutubeTranscriptSegment(
                id=uuid.uuid4(), transcript_id=transcript_row.id, segment_index=i,
                start_second=seg["start"], end_second=seg["start"] + seg["duration"],
                duration=seg["duration"], text=seg["text"],
                created_at=now,
            ))

        await db.commit()

    return {"checked": len(posts), **counts}
