"""
Twitter/X adapter.

Field yang tersedia dari Apify (dikonfirmasi live ke data produksi,
lihat app/services/twitter/pipeline_service.py):
  metadata: { likes, views, retweets, comments, quotes, followers }

`retweets` = fitur "share/repost" Twitter -- dipetakan ke `shares` di sini
(nama field aslinya BEDA dari platform lain, ini yang diselaraskan).

Field yang TIDAK tersedia: saves (Twitter tidak expose jumlah bookmark
publik), replies (balasan dihitung sebagai komentar biasa, bukan komponen
terpisah -- lihat catatan "quotes" di bawah), clicks (tidak ada tracking klik link).

Catatan: `quotes` (quote-tweet) ada di data mentah tapi SENGAJA belum
dipetakan ke field manapun -- perlu keputusan eksplisit apakah quote-tweet
dihitung sebagai "share" tambahan atau komponen terpisah, belum diminta.
"""

from .base import PlatformAdapter, PlatformFieldMap


class TwitterAdapter(PlatformAdapter):
    platform = "twitter"
    field_map = PlatformFieldMap(
        views="views",
        likes="likes",
        shares="retweets",     # nama asli Twitter utk share/repost
        saves="saves",         # belum tersedia
        replies="replies",     # belum tersedia (lihat catatan "quotes" di atas)
        clicks="clicks",       # belum tersedia
        unavailable=["saves", "replies", "clicks"],
    )
