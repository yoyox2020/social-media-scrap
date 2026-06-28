"""
YouTube adapter.

Field yang tersedia dari EnsembleData scraper:
  metadata: { views, likes, comments, duration, thumbnail, description }

Field yang TIDAK tersedia (belum di-scrape): shares, saves, replies, clicks.
Saat TikTok/Instagram ditambah, buat file adapter masing-masing dengan field map-nya sendiri.
"""

from .base import PlatformAdapter, PlatformFieldMap


class YouTubeAdapter(PlatformAdapter):
    platform = "youtube"
    field_map = PlatformFieldMap(
        views="views",
        likes="likes",
        shares="shares",       # belum tersedia dari scraper
        saves="saves",         # belum tersedia
        replies="replies",     # belum tersedia
        clicks="clicks",       # belum tersedia
        unavailable=["shares", "saves", "replies", "clicks"],
    )
