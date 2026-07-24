"""Wrapper `youtube-transcript-api` + proxy Webshare (2026-07-25) --
DIVERIFIKASI LIVE (bukan tebakan): TANPA proxy, endpoint fetch isi
transkrip 100% diblokir YouTube (cloud IP). DENGAN proxy
`WebshareProxyConfig` (username WAJIB pakai suffix "-rotate"), 3/3 video
asli di DB kita berhasil diambil isi teks+timestamp-nya.

Prioritas SESUAI permintaan user: Manual Subtitle -> Auto-Generated
Subtitle. TIDAK ADA fallback Whisper (keputusan eksplisit user, skip
speech-to-text -- video tanpa caption sama sekali ditandai
source='unavailable', BUKAN diproses lebih lanjut)."""
from __future__ import annotations

from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable
from youtube_transcript_api.proxies import WebshareProxyConfig

PREFERRED_LANGUAGES = ["id", "en"]


def _build_api(proxy_username: str, proxy_password: str) -> YouTubeTranscriptApi:
    return YouTubeTranscriptApi(proxy_config=WebshareProxyConfig(
        proxy_username=proxy_username,
        proxy_password=proxy_password,
    ))


async def fetch_transcript(video_id: str, proxy_username: str, proxy_password: str) -> dict[str, Any]:
    """Return dict siap disimpan ke DB: {status, language, language_code,
    is_generated, is_translated, segments: [...]} ATAU {status:"unavailable"/
    "error", error_message}. TIDAK raise -- pemanggil (backfill agent)
    lanjut ke video berikutnya apa pun hasilnya."""
    import asyncio

    def _sync_fetch() -> dict[str, Any]:
        api = _build_api(proxy_username, proxy_password)

        transcript_list = api.list(video_id)

        chosen = None
        # 1) Manual subtitle bahasa preferensi -> 2) manual bahasa APA PUN
        # -> 3) auto-generated bahasa preferensi -> 4) auto-generated APA PUN.
        try:
            chosen = transcript_list.find_manually_created_transcript(PREFERRED_LANGUAGES)
        except NoTranscriptFound:
            pass
        if chosen is None:
            for t in transcript_list:
                if not t.is_generated:
                    chosen = t
                    break
        if chosen is None:
            try:
                chosen = transcript_list.find_generated_transcript(PREFERRED_LANGUAGES)
            except NoTranscriptFound:
                pass
        if chosen is None:
            for t in transcript_list:
                chosen = t
                break

        if chosen is None:
            return {"status": "unavailable", "error_message": "Tidak ada transcript (manual maupun auto-generated) sama sekali"}

        fetched = chosen.fetch()
        segments = [
            {"text": s.text, "start": s.start, "duration": s.duration}
            for s in fetched
        ]
        return {
            "status": "generated" if chosen.is_generated else "manual",
            "language": chosen.language,
            "language_code": chosen.language_code,
            "is_generated": chosen.is_generated,
            "is_translated": chosen.is_translatable if hasattr(chosen, "is_translatable") else False,
            "segments": segments,
        }

    try:
        return await asyncio.to_thread(_sync_fetch)
    except (TranscriptsDisabled, VideoUnavailable) as exc:
        return {"status": "unavailable", "error_message": f"{type(exc).__name__}: {str(exc)[:300]}"}
    except Exception as exc:
        return {"status": "error", "error_message": f"{type(exc).__name__}: {str(exc)[:300]}"}
