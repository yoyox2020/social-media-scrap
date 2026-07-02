"""
Facebook Graph API connector.
Menggunakan Facebook Graph API v21.0 secara langsung (bukan EnsembleData).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.shared.exceptions import ExternalAPIError

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class FacebookConnector:
    def __init__(self, access_token: str, timeout: int = 30):
        self.access_token = access_token
        self.timeout = timeout

    def _p(self, extra: dict | None = None) -> dict:
        return {"access_token": self.access_token, **(extra or {})}

    async def get_me(self) -> dict[str, Any]:
        """Info pemilik token."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{GRAPH_API_BASE}/me", params=self._p({"fields": "id,name,email,picture"}))
            self._check(r)
            return r.json()

    async def get_page_info(self, identifier: str) -> dict[str, Any]:
        """Info page/profil by username atau page_id."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/{identifier}",
                params=self._p({
                    "fields": "id,name,username,fan_count,followers_count,about,category,website,link,picture.type(large)",
                }),
            )
            self._check(r)
            return r.json()

    async def get_page_posts(self, page_id: str, limit: int = 10) -> dict[str, Any]:
        """Post dari page."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/{page_id}/posts",
                params=self._p({
                    "fields": "id,message,story,created_time,full_picture,permalink_url,"
                              "likes.summary(true),comments.summary(true),shares",
                    "limit": limit,
                }),
            )
            self._check(r)
            return r.json()

    async def get_user_feed(self, user_id: str = "me", limit: int = 10) -> dict[str, Any]:
        """Feed user (requires user_posts permission)."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/{user_id}/feed",
                params=self._p({
                    "fields": "id,message,story,created_time,full_picture,permalink_url,"
                              "likes.summary(true),comments.summary(true)",
                    "limit": limit,
                }),
            )
            self._check(r)
            return r.json()

    async def get_post_comments(self, post_id: str, limit: int = 25) -> dict[str, Any]:
        """Komentar pada sebuah post."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/{post_id}/comments",
                params=self._p({
                    "fields": "id,message,from,created_time,like_count",
                    "limit": limit,
                    "summary": "true",
                }),
            )
            self._check(r)
            return r.json()

    async def search_pages(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Cari page berdasarkan keyword."""
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(
                f"{GRAPH_API_BASE}/search",
                params=self._p({
                    "q": query,
                    "type": "page",
                    "fields": "id,name,fan_count,category,link,picture.type(small)",
                    "limit": limit,
                }),
            )
            self._check(r)
            return r.json()

    @staticmethod
    def extract_posts(raw: dict) -> list[dict]:
        return raw.get("data", [])

    @staticmethod
    def extract_comments(raw: dict) -> list[dict]:
        return raw.get("data", [])

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code != 200:
            try:
                err = response.json().get("error", {})
                msg = err.get("message", response.text[:300])
            except Exception:
                msg = response.text[:300]
            raise ExternalAPIError(service="FacebookGraphAPI", message=f"HTTP {response.status_code}: {msg}")
