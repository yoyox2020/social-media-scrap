"""
Interface provider pencarian Facebook — cari & scrape sebuah page/profile
berdasarkan identifier (username/slug), sama pola dengan
app/services/instagram/providers/base.py.

Provider BARU cukup implement class ini + daftarkan di PROVIDERS
(providers/registry.py) — tidak ada perubahan di pipeline_service.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseFacebookSearchProvider(ABC):
    name: str

    @abstractmethod
    async def search_profile(
        self, identifier: str, max_posts: int, max_comments: int
    ) -> list[dict]:
        """Return baris dalam bentuk standar: postUrl/postDescription/
        postTimestamp/postLikesCount/postCommentsCount/commentText/
        commentAuthor/commentTimestamp/profileFollowers/..."""
