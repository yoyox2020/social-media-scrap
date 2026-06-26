"""
Google Trends RSS connector.

Mengambil trending topics dari Google Trends RSS feed.
Tidak memerlukan API key — menggunakan feed publik.

Standalone usage:
    python -m app.integrations.google_trends.connector
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import feedparser


GEO = Literal["ID", "US", "GB", "JP", "SG", "MY", "AU"]
PERIOD = Literal["4h", "24h", "48h", "7d"]

_RSS_BASE = "https://trends.google.com/trending/rss"


@dataclass
class TrendingItem:
    rank: int
    title: str
    traffic: str
    description: str
    published_at: datetime | None
    geo: str
    period: str


@dataclass
class TrendingResult:
    geo: str
    period: str
    fetched_at: datetime
    items: list[TrendingItem] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        return [item.title for item in self.items]


def fetch_trending(
    geo: str = "ID",
    period: str = "24h",
    limit: int = 10,
) -> TrendingResult:
    """
    Ambil trending topics dari Google Trends RSS.

    Args:
        geo:    Kode negara ISO 2-huruf (ID, US, dll.)
        period: Jendela waktu — 4h, 24h, 48h, 7d
        limit:  Maksimum item yang dikembalikan

    Returns:
        TrendingResult berisi list TrendingItem
    """
    url = f"{_RSS_BASE}?geo={geo}&hl=id-ID&cd={period}&limit={limit}"
    feed = feedparser.parse(url)

    result = TrendingResult(
        geo=geo,
        period=period,
        fetched_at=datetime.now(timezone.utc),
    )

    for idx, entry in enumerate(feed.entries[:limit], start=1):
        traffic = getattr(entry, "ht_approx_traffic", "") or getattr(entry, "traffic", "")
        published_at = _parse_published(getattr(entry, "published", None))
        description = _strip_html(getattr(entry, "summary", "") or "")

        result.items.append(
            TrendingItem(
                rank=idx,
                title=_fix_encoding(entry.title),
                traffic=str(traffic),
                description=description[:300],
                published_at=published_at,
                geo=geo,
                period=period,
            )
        )

    return result


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _fix_encoding(text: str) -> str:
    """Perbaiki mojibake latin1→utf8 dari feedparser (mis. 'pÃ©pÃ©' → 'pépé')."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = fetch_trending(geo="ID", period="24h", limit=10)
    print("=" * 70)
    print(f"Google Trends — {result.geo}  |  {result.period}")
    print(f"Diambil: {result.fetched_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)
    for item in result.items:
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(item.rank, f"{item.rank}.")
        print(f"{medal} {item.title}  [{item.traffic}]")
        if item.description:
            print(f"   {item.description[:120]}")
    print("=" * 70)
