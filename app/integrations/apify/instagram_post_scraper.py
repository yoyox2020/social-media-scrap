"""
Apify Instagram scraper -- pengganti `instagram.py::scrape_instagram_via_apify()`
sbg provider UTAMA scrape-per-username (2026-07-20). Alasan ganti: actor lama
(`ycQuEFDDZmgX7BAsL`) TERBUKTI tidak pernah kirim field foto sama sekali
(0/41 post produksi punya thumbnail, dikonfirmasi live query DB) -- actor
ini (`apify/instagram-post-scraper`) SUDAH di-live-test sebelumnya (lihat
memory reference_instagram_post_scraper_actor) dan diverifikasi ulang
2026-07-20: field `displayUrl` SELALU ada.

Bentuk hasil (per baris = SATU POST, komentar NESTED di `latestComments`,
BEDA dari actor lama yg 1 baris = 1 pasangan post+comment):
    {
      "shortCode": "...", "url": "...", "caption": "...", "timestamp": "...",
      "likesCount": int, "commentsCount": int, "displayUrl": "...",
      "ownerUsername": "...", "latestComments": [
        {"text": "...", "ownerUsername": "...", "timestamp": "...", "likesCount": int}, ...
      ]
    }

Keterbatasan TERUKUR (bukan bug kita): `latestComments` konsisten cuma
~14-15 per post berapa pun `resultsLimit` di-set (parameter itu mengatur
jumlah POST, bukan komentar) -- belum ketemu cara pasti menaikkan cakupan
komentar utk actor ini.
"""
from __future__ import annotations

from typing import Any

from app.integrations.apify.rotation import call_apify_actor

ACTOR_ID = "apify/instagram-post-scraper"


async def scrape_instagram_posts_via_apify(username: str, results_limit: int = 12) -> list[dict[str, Any]]:
    """Scrape post (+komentar nested) Instagram untuk satu username via Apify.
    2026-07-20: pakai call_apify_actor() (pool token + rotasi otomatis)."""
    return await call_apify_actor(ACTOR_ID, {"username": [username], "resultsLimit": results_limit})
