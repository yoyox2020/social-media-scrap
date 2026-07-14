"""
Instagram adapter.

Field yang tersedia dari Apify (dikonfirmasi live ke data produksi,
lihat app/services/instagram/pipeline_service.py -- metadata_ diisi
{ likes, comments, shortcode, photo_url, source }):
  metadata: { likes, comments }

Field yang TIDAK tersedia dari jalur scraping yang AKTIF sekarang: views,
shares, saves, replies, clicks. Catatan penting: 10 dari 61 post instagram
di DB PUNYA field "views" (angka nyata, sisa jalur scraping LAMA sebelum
redesign Smart Search) -- SENGAJA tetap ditandai unavailable di sini supaya
exposure Instagram konsisten 0 utk SEMUA post (bukan campuran data lama
vs baru yang menyesatkan kalau dirata-rata/dijumlah begitu saja).
"""

from .base import PlatformAdapter, PlatformFieldMap


class InstagramAdapter(PlatformAdapter):
    platform = "instagram"
    field_map = PlatformFieldMap(
        views="views",
        likes="likes",
        shares="shares",
        saves="saves",
        replies="replies",
        clicks="clicks",
        unavailable=["views", "shares", "saves", "replies", "clicks"],
    )
