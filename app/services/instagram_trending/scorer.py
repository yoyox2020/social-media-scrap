"""
Trending score calculator untuk Instagram accounts.

Formula:
  engagement_rate = (avg_likes + avg_comments × 2) / max(followers, 1) × 100
  virality_score  = avg_views / max(followers, 1)
  trending_score  = engagement_rate × 0.50 + virality_score × 0.50

Semua nilai dari posts yang sudah ada di DB (platform='instagram', author=username).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrendingScore:
    engagement_rate: float
    virality_score: float
    trending_score: float


def calculate(posts_metadata: list[dict], followers: int) -> TrendingScore:
    """
    Hitung trending score dari list metadata post.

    posts_metadata: list of dict {"likes": int, "comments": int, "views": int}
    followers: jumlah follower akun
    """
    if not posts_metadata:
        return TrendingScore(0.0, 0.0, 0.0)

    n = len(posts_metadata)
    avg_likes    = sum(p.get("likes", 0) or 0 for p in posts_metadata) / n
    avg_comments = sum(p.get("comments", 0) or 0 for p in posts_metadata) / n
    avg_views    = sum(p.get("views", 0) or 0 for p in posts_metadata) / n

    base = max(followers, 1)
    engagement_rate = (avg_likes + avg_comments * 2) / base * 100
    virality_score  = avg_views / base

    trending_score = engagement_rate * 0.50 + virality_score * 0.50

    return TrendingScore(
        engagement_rate=round(engagement_rate, 4),
        virality_score=round(virality_score, 4),
        trending_score=round(trending_score, 4),
    )
