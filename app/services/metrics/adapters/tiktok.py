"""
TikTok adapter.

Field yang tersedia dari Apify (dikonfirmasi live ke data produksi,
lihat app/services/tiktok/pipeline_service.py):
  metadata: { likes, views, shares, comments, collects, nickname }

`collects` = fitur "save/bookmark" TikTok -- dipetakan ke `saves` di sini
(bukan nama field asli "saves", field_map yang menerjemahkannya).

Field yang TIDAK tersedia: replies (balasan komentar tidak dihitung
terpisah), clicks (tidak ada tracking klik link).
"""

from .base import PlatformAdapter, PlatformFieldMap


class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"
    field_map = PlatformFieldMap(
        views="views",
        likes="likes",
        shares="shares",
        saves="collects",      # nama asli TikTok utk save/bookmark
        replies="replies",     # belum tersedia
        clicks="clicks",       # belum tersedia
        unavailable=["replies", "clicks"],
    )
