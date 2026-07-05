"""
Provider EnsembleData — menghidupkan lagi chain connector Instagram yang dulu
dihapus di commit migrasi Apify (lihat `git show 0a9f291^:app/integrations/instagram/connector.py`
untuk kode aslinya), dinormalisasi ke bentuk baris yang sama dengan Apify
supaya pipeline_service.py tidak perlu tahu bedanya.

CATATAN: subscription EnsembleData sedang expired (HTTP 493) per pengecekan
terakhir — provider ini akan gagal cepat (ExternalAPIError) sampai subscription
diperbarui. Itu perilaku yang diharapkan: registry.py akan otomatis fallback
ke provider berikutnya di `instagram_search_provider_order`.
"""
from __future__ import annotations

from typing import Any

from app.services.instagram.providers.base import BaseInstagramSearchProvider


class EnsembleDataInstagramProvider(BaseInstagramSearchProvider):
    name = "ensembledata"

    async def search_profile(
        self, username: str, max_posts: int, max_comments: int
    ) -> list[dict[str, Any]]:
        from app.integrations.ensemble_data.client import EnsembleDataClient
        from app.integrations.ensemble_data.endpoints import InstagramEndpoints
        from app.shared.config import settings
        from app.shared.exceptions import ExternalAPIError

        if not settings.ensemble_data_api_token:
            raise ExternalAPIError(service="EnsembleData", message="ENSEMBLE_DATA_API_TOKEN belum di-set")

        async with EnsembleDataClient() as client:
            user_info = await client.get(InstagramEndpoints.USER_INFO.path, params={"username": username})
            user_data = user_info.get("data") or {}
            user_id = user_data.get("pk") or user_data.get("id")
            if not user_id:
                raise ExternalAPIError(service="EnsembleData", message=f"user_id tidak ditemukan untuk {username}")

            posts_raw = await client.get(InstagramEndpoints.USER_POSTS.path, params={"user_id": user_id, "depth": 1})
            posts = ((posts_raw.get("data") or {}).get("collector") or [])[:max_posts]

            rows: list[dict[str, Any]] = []
            for post in posts:
                shortcode = post.get("shortcode") or post.get("code") or ""
                media_id = str(post.get("pk") or post.get("id") or "").split("_")[0]
                caption = post.get("caption")
                caption_text = caption.get("text", "") if isinstance(caption, dict) else (caption or "")

                base = {
                    "postUrl": f"https://www.instagram.com/p/{shortcode}/",
                    "postDescription": caption_text,
                    "postTimestamp": post.get("taken_at"),
                    "postLikesCount": post.get("like_count", 0),
                    "postCommentsCount": post.get("comment_count", 0),
                    "profileFollowers": user_data.get("follower_count", 0),
                    "profileDescription": user_data.get("biography", ""),
                }

                comments: list[dict[str, Any]] = []
                if media_id and max_comments > 0:
                    comments_raw = await client.get(
                        InstagramEndpoints.POST_COMMENTS.path,
                        params={"media_id": media_id, "cursor": "", "sorting": "popular"},
                    )
                    comments = ((comments_raw.get("data") or {}).get("collector") or [])[:max_comments]

                if not comments:
                    rows.append({**base, "commentText": "", "commentAuthor": "", "commentTimestamp": None})
                for c in comments:
                    rows.append({
                        **base,
                        "commentText": c.get("text", ""),
                        "commentAuthor": (c.get("user") or {}).get("username", ""),
                        "commentTimestamp": c.get("created_at"),
                    })

            return rows
