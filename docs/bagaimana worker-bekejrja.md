
Semua berjalan sempurna. Sekarang jawaban lengkap untuk pertanyaanmu:

Hasil Test
Monitor endpoint sekarang berisi data nyata:

worker_alive: true
Scraping ahok: 19 video diambil, 18 baru, 1 duplikat, via EnsembleData, selesai dalam 2.4 detik
Komentar masih 0 di monitor karena comment tasks masih jalan paralel (lihat log — 4 worker sedang fetch komentar)
Cara Kerja Scraping Jam 12 Siang
Alurnya 2 tahap:


Celery Beat (jam 12.00 WIB)
  │
  ▼
fetch_trending_youtube_task
  │  → Ambil 10 topik dari Google Trends (geo=ID, period=24h)
  │  → Simpan ke tabel trending_topics
  │  → Buat/update Keyword di tabel keywords
  │
  ▼  (untuk setiap keyword)
collect_youtube_pipeline_task(keyword_id)
  │  → Cari video di YouTube via EnsembleData API
  │  → Jika EnsembleData HTTP 495 (quota habis) → fallback ke YouTube Data API v3
  │  → Simpan video ke tabel posts
  │  → Catat ke scrape_runs
  │
  ▼  (untuk setiap video baru)
collect_youtube_comments_task(post_id)
     → Ambil komentar semua halaman via cursor
     → Simpan ke tabel comments
     → Jalankan analisis sentimen lexicon