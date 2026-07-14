"""
Base adapter — kontrak yang harus diikuti setiap platform.

Untuk tambah platform baru (TikTok, Instagram, News):
1. Buat file adapters/tiktok.py
2. Subclass PlatformAdapter
3. Override field_map dan engagement_fields
4. Daftarkan di ADAPTER_REGISTRY di calculator.py
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlatformFieldMap:
    """
    Mapping kolom engagement dari metadata JSON per platform.
    Setiap platform simpan nama field berbeda — di sini kita normalisasi.
    """
    views: str = "views"           # impressions / tayangan
    likes: str = "likes"           # like / love
    shares: str = "shares"         # repost / share / retweet
    saves: str = "saves"           # bookmark / save
    replies: str = "replies"       # reply / balasan
    clicks: str = "clicks"         # link click / cta click

    # Field yang tidak tersedia di platform ini (kosongkan = 0)
    unavailable: list[str] = field(default_factory=list)


class PlatformAdapter:
    """
    Base class adapter platform. Subclass ini untuk setiap platform.
    Semua perhitungan akhir tetap di MetricsCalculator — adapter hanya
    bertanggung jawab mengekstrak angka dari raw metadata.
    """
    platform: str = "unknown"
    field_map: PlatformFieldMap = PlatformFieldMap()

    def extract_views(self, metadata: dict[str, Any]) -> int:
        if "views" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.views, 0)
        return self._to_int(val)

    def extract_likes(self, metadata: dict[str, Any]) -> int:
        if "likes" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.likes, 0)
        return self._to_int(val)

    def extract_shares(self, metadata: dict[str, Any]) -> int:
        if "shares" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.shares, 0)
        return self._to_int(val)

    def extract_saves(self, metadata: dict[str, Any]) -> int:
        if "saves" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.saves, 0)
        return self._to_int(val)

    def extract_replies(self, metadata: dict[str, Any]) -> int:
        if "replies" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.replies, 0)
        return self._to_int(val)

    def extract_clicks(self, metadata: dict[str, Any]) -> int:
        if "clicks" in self.field_map.unavailable:
            return 0
        val = metadata.get(self.field_map.clicks, 0)
        return self._to_int(val)

    def extract_engagement(self, metadata: dict[str, Any], comment_count_db: int = 0) -> int:
        """
        Total engagement = likes + komentar (DB) + shares + saves + replies + clicks.
        comment_count_db = jumlah komentar yang benar-benar tersimpan di tabel comments.
        """
        return (
            self.extract_likes(metadata)
            + comment_count_db
            + self.extract_shares(metadata)
            + self.extract_saves(metadata)
            + self.extract_replies(metadata)
            + self.extract_clicks(metadata)
        )

    def engagement_breakdown(self, metadata: dict[str, Any], comment_count_db: int = 0) -> dict:
        """Rincian per komponen engagement — untuk transparansi di dashboard."""
        return {
            "likes":    self.extract_likes(metadata),
            "comments": comment_count_db,
            "shares":   self.extract_shares(metadata),
            "saves":    self.extract_saves(metadata),
            "replies":  self.extract_replies(metadata),
            "clicks":   self.extract_clicks(metadata),
            "unavailable_fields": self.field_map.unavailable,
        }

    @staticmethod
    def _to_int(val: Any) -> int:
        if val is None:
            return 0
        try:
            return int(str(val).replace(",", "").split(".")[0])
        except (ValueError, AttributeError):
            return 0
