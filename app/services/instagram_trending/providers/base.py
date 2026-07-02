"""
Base class untuk Instagram trending discovery providers.

Untuk menambah provider baru (RapidAPI, SocialBlade, dll):
  1. Buat file baru di providers/
  2. Inherit BaseDiscoveryProvider
  3. Implement discover()
  4. Daftarkan di service.py PROVIDERS dict
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseDiscoveryProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def discover(self, hashtags: list[str], limit: int = 20) -> list[dict]:
        """
        Cari trending usernames dari hashtag.

        Returns list of:
        {
            "username":  str,
            "display_name": str,
            "followers": int,
            "likes_hint": int,      # likes dari post yang ditemukan
            "comments_hint": int,
            "views_hint": int,
            "discovered_via": str,  # hashtag asal
        }
        """
