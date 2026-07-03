"""
Discovery provider menggunakan EnsembleData Instagram Search.

Search hashtag #indonesia, #viral, #fyp → extract username + hint metrics
dari post yang ditemukan.
"""
from __future__ import annotations

import logging

from app.integrations.ensemble_data.client import EnsembleDataClient
from app.integrations.ensemble_data.endpoints import InstagramEndpoints
from app.integrations.instagram.connector import InstagramConnector
from app.services.instagram_trending.providers.base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)

# Hashtag default untuk discovery Indonesia
DEFAULT_HASHTAGS = ["#indonesia", "#viral", "#fyp", "#trending", "#indonesiatrending"]


class EnsembleDataDiscovery(BaseDiscoveryProvider):
    name = "ensembledata"

    async def discover(self, hashtags: list[str] | None = None, limit: int = 20) -> list[dict]:
        tags = hashtags or DEFAULT_HASHTAGS
        results: dict[str, dict] = {}  # username → data (dedup)

        async with EnsembleDataClient() as client:
            connector = InstagramConnector(client)

            for tag in tags:
                try:
                    raw = await connector.search(tag)
                    items = self._extract_items(raw)

                    for item in items[:limit]:
                        username = self._get_username(item)
                        if not username or username in results:
                            continue

                        likes    = item.get("like_count") or item.get("likes", 0) or 0
                        comments = item.get("comment_count") or item.get("comments", 0) or 0
                        views    = item.get("play_count") or item.get("view_count") or item.get("views", 0) or 0
                        followers = self._get_followers(item)

                        results[username] = {
                            "username":       username,
                            "display_name":   self._get_display_name(item),
                            "followers":      followers,
                            "likes_hint":     likes,
                            "comments_hint":  comments,
                            "views_hint":     views,
                            "discovered_via": tag,
                        }

                except Exception as exc:
                    logger.warning("EnsembleDataDiscovery hashtag=%s error=%s", tag, exc)
                    continue

        return list(results.values())

    @staticmethod
    def _extract_items(raw: dict) -> list[dict]:
        # EnsembleData search bisa return berbagai format
        for key in ("results", "data", "items", "posts", "users", "collector"):
            if isinstance(raw.get(key), list):
                return raw[key]
        if isinstance(raw, list):
            return raw
        return []

    @staticmethod
    def _get_username(item: dict) -> str:
        # Post → user field
        user = item.get("user") or item.get("owner") or {}
        return (
            user.get("username")
            or item.get("username")
            or ""
        ).strip().lstrip("@").lower()

    @staticmethod
    def _get_display_name(item: dict) -> str:
        user = item.get("user") or item.get("owner") or {}
        return user.get("full_name") or user.get("name") or item.get("full_name") or ""

    @staticmethod
    def _get_followers(item: dict) -> int:
        user = item.get("user") or item.get("owner") or {}
        return (
            user.get("follower_count")
            or user.get("followers")
            or item.get("follower_count")
            or 0
        )
