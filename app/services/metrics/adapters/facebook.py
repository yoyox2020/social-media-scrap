"""
Facebook adapter.

Field yang tersedia dari Apify (dikonfirmasi live ke data produksi,
lihat app/services/facebook/pipeline_service.py -- metadata_ cuma pernah
diisi { likes, comments, source } / { likes, comments, followers, source }):
  metadata: { likes, comments }

Field yang TIDAK tersedia sama sekali dari provider: views (tayangan/reach
video), shares, saves, replies (balasan dihitung sebagai komentar biasa),
clicks -- dicek langsung ke DB, 0 dari 43 post facebook punya field-field
itu, beda dari Instagram yang punya sisa data lama tidak konsisten (lihat
instagram.py) -- utk Facebook memang genuinely tidak pernah ada sama sekali.
"""

from .base import PlatformAdapter, PlatformFieldMap


class FacebookAdapter(PlatformAdapter):
    platform = "facebook"
    field_map = PlatformFieldMap(
        views="views",
        likes="likes",
        shares="shares",
        saves="saves",
        replies="replies",
        clicks="clicks",
        unavailable=["views", "shares", "saves", "replies", "clicks"],
    )
