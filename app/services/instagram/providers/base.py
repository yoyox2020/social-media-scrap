"""
Interface provider pencarian Instagram — "pencarian" di sini berarti cari &
scrape sebuah profil by username (bukan discovery by hashtag/keyword; Apify
tidak punya kapabilitas itu, lihat docs/apify-instagram-method.md).

Semua provider mengembalikan baris dalam bentuk yang SAMA (bentuk asli output
Apify Actor `ycQuEFDDZmgX7BAsL`), supaya app/services/instagram/pipeline_service.py
tidak perlu tahu provider mana yang benar-benar dipakai:

    {
      "postUrl": "...", "postDescription": "...", "postTimestamp": "...",
      "postLikesCount": int, "postCommentsCount": int,
      "commentText": "...", "commentAuthor": "...", "commentTimestamp": "...",
      "profileFollowers": int, "profileDescription": "...",
    }
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseInstagramSearchProvider(ABC):
    name: str

    @abstractmethod
    async def search_profile(
        self, username: str, max_posts: int, max_comments: int
    ) -> list[dict[str, Any]]:
        """Cari & scrape profil Instagram by username. Return baris dalam
        bentuk seragam (lihat docstring modul ini). Raise ExternalAPIError
        (atau exception apapun) kalau gagal — registry.py yang menangani
        fallback ke provider berikutnya."""
        raise NotImplementedError
