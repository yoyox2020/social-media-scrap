"""
Backfill posts.published_at untuk video YouTube yang nilainya masih NULL.

Cara kerja:
  - Baca posts YouTube yang published_at IS NULL dan punya metadata.published_text
  - Parse relative time ('3 months ago') menggunakan collected_at sebagai titik acuan
  - Update published_at di database

Jalankan:
  docker compose exec api python scripts/backfill_published_at.py
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from app.infrastructure.database.connection import AsyncSessionLocal
from app.services.processing.normalizer import _parse_relative_time
from sqlalchemy import text


async def backfill():
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT id, metadata->>'published_text' AS published_text, collected_at
                FROM posts
                WHERE platform = 'youtube'
                  AND published_at IS NULL
                  AND metadata->>'published_text' IS NOT NULL
                  AND metadata->>'published_text' != ''
            """)
        )
        records = rows.fetchall()

    print(f"Ditemukan {len(records)} post YouTube tanpa published_at")
    updated = 0
    skipped = 0

    async with AsyncSessionLocal() as db:
        for row in records:
            post_id, published_text, collected_at = row
            parsed = _parse_relative_time(published_text, reference=collected_at)
            if parsed:
                await db.execute(
                    text("UPDATE posts SET published_at = :dt WHERE id = :id"),
                    {"dt": parsed, "id": post_id},
                )
                updated += 1
            else:
                print(f"  Tidak bisa parse: '{published_text}' (post_id={post_id})")
                skipped += 1

        await db.commit()

    print(f"\nSelesai: {updated} diupdate, {skipped} dilewati")

    # Tampilkan sampel hasil
    async with AsyncSessionLocal() as db:
        sample = await db.execute(
            text("""
                SELECT
                  content,
                  metadata->>'published_text' AS published_text,
                  published_at::date AS published_date,
                  collected_at::date AS collected_date
                FROM posts
                WHERE platform = 'youtube' AND published_at IS NOT NULL
                ORDER BY published_at DESC
                LIMIT 10
            """)
        )
        print("\nSampel hasil (10 video terbaru berdasarkan tanggal publish):")
        print(f"{'Judul':<50} {'Teks Asli':<20} {'Tanggal Publish':<16} {'Dikumpulkan'}")
        print("-" * 100)
        for r in sample.fetchall():
            judul = (r.content or "")[:48]
            print(f"{judul:<50} {r.published_text:<20} {str(r.published_date):<16} {r.collected_date}")


if __name__ == "__main__":
    asyncio.run(backfill())
