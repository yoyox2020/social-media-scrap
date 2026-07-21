"""
Notifikasi "topik ini lagi viral" -- dipicu Celery Beat tiap jam
(workers.search_topics.hourly_viral_notifications, lihat
app/workers/search_topics_worker.py).

Ambang batas viral per platform DAN jendela waktu (berapa hari ke belakang
post masih dianggap "baru viral") disimpan di REDIS (bukan .env) -- keputusan
eksplisit user: harus bisa diubah LIVE lewat API (PATCH /search/notifications/
thresholds, PATCH /search/notifications/lookback-days) tanpa restart
container apa pun, beda dari .env yang butuh docker compose up -d --no-deps
utk kepakai (lihat memory server_access soal ini). Task per jam SELALU baca
nilai TERKINI dari Redis di tiap run.

Untuk tiap SearchTopic aktif (is_active=True -- TIDAK dibatasi
schedule_recurring, topik non-recurring pun bisa dapat post baru lewat
pencarian manual/queue), tiap platform yang punya ambang batas terdefinisi,
tiap keyword topik: cari post yang cocok keyword (pola ILIKE sama seperti
tier_search.py) DAN metriknya (views/likes, tergantung platform -- FB/IG
TIDAK PERNAH punya views, lihat docs/engagement-dashboard-api.md) melewati
ambang batas DAN belum pernah dinotifikasi utk topik ini
(UniqueConstraint(topic_id, post_id) di model, ON CONFLICT DO NOTHING).

2026-07-20: kalau ada notifikasi baru dlm satu run, sekaligus dikirim ke
WhatsApp (via Fonnte, lihat app/services/whatsapp_notify/) -- SATU pesan
gabungan per run (bukan per notifikasi), best-effort (gagal kirim TIDAK
BOLEH bikin run ini dianggap gagal). Token+nomor tujuan diatur lewat
PATCH /credentials/whatsapp_fonnte_token dan
PATCH /credentials/whatsapp_target_numbers -- kalau belum diisi,
pengiriman di-skip diam-diam (lihat whatsapp_notify/config.py::is_configured()).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.search_topics.models import SearchTopic, TopicNotification
from app.infrastructure.redis.connection import get_redis
from app.services.search_topics.tier_search import _word_and_clause

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "notif:threshold:"
_REDIS_LOOKBACK_KEY = "notif:lookback_days"

# Ditambahkan 2026-07-17: cuma post yg "masih trending" (di-upload/terbit
# dalam N hari terakhir) yang boleh dinotifikasi -- sebelum ini TIDAK ada
# batas waktu sama sekali, jadi run pertama nemuin backlog 127 post viral
# LAMA yg kebetulan baru sekarang dicek (bukan genuinely baru viral). Filter
# pakai `published_at` (tanggal upload ASLI konten, bukan kapan kita
# nyimpannya) -- fallback ke `collected_at` kalau published_at kosong (bbrp
# platform/parsing kadang tidak berhasil isi field ini), supaya post TIDAK
# diam-diam ke-skip cuma krn data tidak lengkap.
#
# Diubah jadi bisa dikonfigurasi lewat API (2026-07-17, permintaan user):
# nilai default di bawah cuma dipakai SEKALI utk seed Redis kalau belum ada
# nilai tersimpan -- sesudahnya Redis yg jadi sumber kebenaran, pola SAMA
# persis dgn get_threshold()/set_threshold(). Frontend atur lewat
# GET/PATCH /search/notifications/lookback-days, efek LANGSUNG di run
# berikutnya tanpa restart apa pun.
DEFAULT_LOOKBACK_DAYS = 30

# Default ambang batas -- dipakai SEKALI kalau Redis belum punya nilai
# (mis. deploy pertama kali). Setelahnya nilai di Redis yang jadi sumber
# kebenaran, defaults ini tidak pernah dibaca ulang.
DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "youtube":   {"metric": "views", "value": 1_000_000},  # sama dgn viral_tracking VIRAL_VIEW_THRESHOLD
    "tiktok":    {"metric": "views", "value": 500_000},
    "twitter":   {"metric": "likes", "value": 10_000},
    "facebook":  {"metric": "likes", "value": 5_000},    # tidak ada views sama sekali
    "instagram": {"metric": "likes", "value": 5_000},    # tidak ada views sama sekali
}

SUPPORTED_NOTIFICATION_PLATFORMS = set(DEFAULT_THRESHOLDS.keys())


async def get_threshold(platform: str) -> dict[str, Any] | None:
    """Baca ambang batas platform dari Redis, seed dgn default kalau belum
    ada. Return None kalau platform tidak didukung (mis. 'news')."""
    if platform not in SUPPORTED_NOTIFICATION_PLATFORMS:
        return None
    redis = await get_redis()
    raw = await redis.get(_REDIS_KEY_PREFIX + platform)
    if raw is not None:
        return json.loads(raw)
    default = DEFAULT_THRESHOLDS[platform]
    await redis.set(_REDIS_KEY_PREFIX + platform, json.dumps(default))
    return default


async def get_all_thresholds() -> dict[str, dict[str, Any]]:
    return {p: await get_threshold(p) for p in SUPPORTED_NOTIFICATION_PLATFORMS}


async def set_threshold(platform: str, metric: str, value: int) -> dict[str, Any]:
    """Update ambang batas -- efeknya LANGSUNG kepakai di run per-jam
    BERIKUTNYA, tanpa perlu restart apa pun (task selalu baca fresh dari
    Redis tiap kali jalan)."""
    if platform not in SUPPORTED_NOTIFICATION_PLATFORMS:
        raise ValueError(f"Platform '{platform}' tidak didukung fitur notifikasi. Pilih dari: {sorted(SUPPORTED_NOTIFICATION_PLATFORMS)}")
    if metric not in ("views", "likes"):
        raise ValueError("metric harus 'views' atau 'likes'")
    if value <= 0:
        raise ValueError("value harus > 0")

    redis = await get_redis()
    payload = {"metric": metric, "value": value}
    await redis.set(_REDIS_KEY_PREFIX + platform, json.dumps(payload))
    return payload


async def get_lookback_days() -> int:
    """Baca jendela waktu (hari) dari Redis, seed dgn default kalau belum
    ada. Pola SAMA persis dgn get_threshold()."""
    redis = await get_redis()
    raw = await redis.get(_REDIS_LOOKBACK_KEY)
    if raw is not None:
        return int(raw)
    await redis.set(_REDIS_LOOKBACK_KEY, str(DEFAULT_LOOKBACK_DAYS))
    return DEFAULT_LOOKBACK_DAYS


async def set_lookback_days(days: int) -> int:
    """Update jendela waktu -- efeknya LANGSUNG kepakai di run per-jam
    BERIKUTNYA, tanpa perlu restart apa pun."""
    if days <= 0:
        raise ValueError("days harus > 0")
    redis = await get_redis()
    await redis.set(_REDIS_LOOKBACK_KEY, str(days))
    return days


async def run_hourly_topic_notifications(db: AsyncSession) -> dict:
    """Entry point dipanggil worker Celery. Return ringkasan run."""
    topics = (await db.scalars(
        select(SearchTopic).where(SearchTopic.is_active == True)  # noqa: E712
    )).all()

    topics_checked = 0
    notifications_created = 0
    errors: list[str] = []
    created_details: list[dict] = []
    lookback_days = await get_lookback_days()

    for topic in topics:
        topics_checked += 1
        # Query langsung (bukan selectinload relationship spt rescan_service.py)
        # krn di sini cuma butuh keyword_text, lebih murah.
        kw_rows = (await db.execute(text(
            "SELECT keyword_text FROM search_topic_keywords WHERE topic_id = :tid"
        ), {"tid": str(topic.id)})).scalars().all()

        for platform in topic.platforms:
            threshold_cfg = await get_threshold(platform)
            if threshold_cfg is None:
                continue  # platform tidak punya metrik viral terdefinisi (mis. news)

            metric = threshold_cfg["metric"]
            min_value = threshold_cfg["value"]

            for kw_text in kw_rows:
                try:
                    created_rows = await _find_and_notify(
                        db, topic.id, platform, kw_text, metric, min_value, lookback_days,
                    )
                    notifications_created += len(created_rows)
                    for row in created_rows:
                        created_details.append({**row, "topic_name": topic.name})
                except Exception as exc:
                    logger.error(
                        "run_hourly_topic_notifications: gagal utk topic=%s platform=%s keyword=%r: %s",
                        topic.name, platform, kw_text, exc,
                    )
                    errors.append(f"{topic.name}/{platform}/{kw_text}: {exc}")

    if created_details:
        # Best-effort -- kirim SATU pesan gabungan (bukan 1 pesan per
        # notifikasi) supaya tidak nge-spam WA kalau kebetulan banyak yg
        # lolos dlm 1 run yg sama. Gagal kirim TIDAK BOLEH bikin run ini
        # dianggap gagal (lihat docstring send_whatsapp_message()).
        try:
            from app.services.whatsapp_notify.client import send_whatsapp_message

            await send_whatsapp_message(_build_whatsapp_message(created_details))
        except Exception as exc:
            logger.warning("run_hourly_topic_notifications: kirim WA gagal: %s", exc)

    result = {
        "topics_checked": topics_checked,
        "notifications_created": notifications_created,
        "errors": errors[:10],
    }
    logger.info("run_hourly_topic_notifications: %s", result)
    return result


def _build_whatsapp_message(created_details: list[dict]) -> str:
    lines = [f"Notifikasi topik viral baru ({len(created_details)}):", ""]
    for i, d in enumerate(created_details, start=1):
        lines.append(f"{i}. [{d['topic_name']}/{d['platform']}] {d['keyword_text']} -- {d['metric_type']} {d['metric_value']:,}")
        if d.get("title"):
            lines.append(f"   {d['title']}")
        if d.get("url"):
            lines.append(f"   {d['url']}")
        lines.append("")
    return "\n".join(lines).strip()


async def _find_and_notify(
    db: AsyncSession,
    topic_id,
    platform: str,
    keyword_text: str,
    metric: str,
    min_value: int,
    lookback_days: int,
) -> list[dict]:
    """Cari post yg cocok keyword+platform+ambang-batas+jendela waktu
    (lookback_days, dari Redis lewat get_lookback_days()) utk SATU topik,
    insert TopicNotification utk yg belum pernah dinotifikasi (ON CONFLICT DO
    NOTHING lewat UniqueConstraint(topic_id, post_id) -- aman dari race kalau
    kebetulan ada 2 proses jalan bersamaan). Return detail baris yg BENAR2
    ke-insert (pakai RETURNING, bukan `values` yg diajukan -- ON CONFLICT
    bisa skip sebagian), dipakai caller utk compose pesan WhatsApp."""
    window_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    params: dict = {"platform": platform, "min_value": min_value, "window_start": window_start}
    match_clause = _word_and_clause("content", keyword_text, params, "kw")

    rows = (await db.execute(text(f"""
        SELECT p.id, p.content, p.author, p.url, p.metadata,
               COALESCE(p.published_at, p.collected_at) AS effective_published_at
        FROM posts p
        WHERE {match_clause}
          AND p.platform = :platform
          AND COALESCE((p.metadata->>'{metric}')::bigint, 0) >= :min_value
          AND COALESCE(p.published_at, p.collected_at) >= :window_start
          AND NOT EXISTS (
              SELECT 1 FROM topic_notifications tn
              WHERE tn.topic_id = :topic_id AND tn.post_id = p.id
          )
    """), {**params, "topic_id": str(topic_id)})).mappings().all()

    if not rows:
        return []

    now = datetime.now(timezone.utc)
    values = [
        {
            "id": uuid.uuid4(),
            "topic_id": topic_id,
            "platform": platform,
            "post_id": r["id"],
            "keyword_text": keyword_text,
            "metric_type": metric,
            "metric_value": int((r["metadata"] or {}).get(metric, 0) or 0),
            "threshold": min_value,
            "title": r["content"],
            "author": r["author"],
            "url": r["url"],
            "post_published_at": r["effective_published_at"],
            "is_read": False,
            "created_at": now,
            "updated_at": now,
        }
        for r in rows
    ]

    stmt = insert(TopicNotification.__table__).values(values).on_conflict_do_nothing(
        index_elements=["topic_id", "post_id"]
    ).returning(
        TopicNotification.platform, TopicNotification.keyword_text,
        TopicNotification.metric_type, TopicNotification.metric_value,
        TopicNotification.title, TopicNotification.url,
    )
    result = (await db.execute(stmt)).mappings().all()
    await db.commit()
    return [dict(r) for r in result]
