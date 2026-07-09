# Alur GET /facebook/posts/search

Sumber: `app/api/v1/facebook/router.py:303-483` (dibaca & dianalisis 2026-07-09).

Jawaban atas pertanyaan: **kalau keyword tidak ada di database, apakah endpoint ini melakukan scrape (POST ke Apify)?**

**Ya — tapi bukan HTTP POST terpisah, melainkan pemanggilan langsung ke service Apify di dalam request GET yang sama.** Ada 3 tingkat, jalan berurutan:

## Tingkat 1 — Database lokal (`router.py:337-373`)

Cari `posts.content ILIKE %keyword%` OR hashtag exact di `entities`.
Kalau ketemu → langsung `return`, **tidak ada scrape sama sekali**. Ini jalur cepat (contoh: keyword "Sayang Mama" di sesi sebelumnya).

## Tingkat 2 — Cocokkan ke `trend_recommendations` (`router.py:385-410`)

Kalau `q` diisi tapi nihil di DB → cari topik yang `topic ILIKE %keyword%` di `trend_recommendations`, urut **`recommendation_date` terbaru dulu, baru `score`** (bukan score-only — ini hasil fix terakhir, bagian dari fitur 3-tier search).

Ambil akun Facebook pertama yang match dari `related_accounts` → panggil `_scrape_now_and_respond()` → **scrape SEKARANG via Apify** (`scrape_facebook_posts_via_provider`), tandai topik jadi `status='used'` kalau sukses.

## Tingkat 3 — Search langsung ke Facebook (`router.py:412-432`)

Kalau topik juga nihil di `trend_recommendations` (keyword benar-benar baru) → panggil `discover_facebook_topic_by_keyword()`, **actor Apify yang sama dengan `POST /facebook/discover`**, untuk cari akun Facebook nyata yang bahas keyword itu (`max_results=5`).

- Kalau ketemu akun → scrape akun pertama juga lewat `_scrape_now_and_respond`, dengan `source: "scraped_now_external"`.
- Kalau tetap nihil → return `"source": "not_found"` dengan pesan bahwa keyword tidak ketemu di DB, trend_recommendations, maupun search langsung.

## Pengecualian — `q` kosong

Kalau `q` dikosongkan (mode "tampilkan semua post Facebook lokal"), fallback tingkat 2 & 3 **tidak pernah jalan** — karena tidak ada keyword untuk dicocokkan ke topik. Langsung `return` kosong dengan `message` (`router.py:377-383`), meski dipersempit `date_from`/`date_to`.

## Detail `_scrape_now_and_respond()` (`router.py:435-483`)

Dipakai bareng oleh tingkat 2 & 3:

1. Bikin `ScrapeRun` baru, `status="running"`, commit segera supaya kelihatan live di monitor.
2. Panggil `scrape_facebook_posts_via_provider(identifier, max_posts=5, max_comments=5)` — **cuma Apify, TIDAK ADA fallback EnsembleData** (beda dari Instagram/TikTok). `app/services/facebook/providers/registry.py:27-30` cuma daftar 1 provider (`apify`); `facebook_search_provider_order` default `"apify"` saja (`app/shared/config.py:110`). Kalau Apify gagal/kuota habis, TIDAK ada provider kedua yang otomatis dicoba.
3. Update `ScrapeRun` jadi `success`/`failed` tergantung `posts_scraped > 0`.
4. Kalau sukses & ada `mark_topic` (tingkat 2 saja) → topik ditandai `used`.
5. Query ulang post terbaru milik `identifier` dari DB, susun response via `_build_fb_search_items`.
6. Response selalu ada catatan: sentimen post baru diproses async (Celery), mungkin belum muncul kalau baru saja discrape.

## Ringkasan urutan

```
DB lokal → trend_recommendations (tanggal terbaru dulu, lalu score) → search eksternal langsung ke Facebook (Apify discover)
```

Ini konsisten dengan fitur "3-tier search" yang sudah selesai & di-deploy (commit `1d41e34`) — bukan cuma 2 tingkat seperti diagram lama di `fb-aluraristektur.md` (yang berhenti di tingkat 2 dan belum menjawab kasus topik juga tidak ketemu di trend_recommendations).
