Ambil topik dari trend_recommendations (yang skornya tinggi/trending)
Scrape via Apify
Kalau gagal/kuota habis → tetap pending, dicoba lagi besok
Ulangi terus otomatis sampai semua topik pending habis jadi used
Semua proses bisa dipantau — tahu kapan status berubah dari pending ke used

Yang kamu mau	Status
Ambil topik trending dari trend_recommendations	✅ sudah — run_daily_trend_scrape(), urut skor tertinggi
Scrape via Apify	✅ sudah — 1 post + komentar per topik
Gagal/kuota habis → tetap pending, coba lagi besok	✅ sudah — kalau Apify gagal/0 post, status TIDAK diubah, otomatis jadi kandidat lagi besok
Berulang otomatis sampai semua selesai	✅ sudah — Celery Beat jalan tiap hari jam 09:00 WIB, maks 3 topik/hari (instagram_trend_daily_budget)
Monitoring status pending→used	❌ belum ada endpoint khusus — sekarang harus query manual (GET /trend-recommendations atau langsung psql)


