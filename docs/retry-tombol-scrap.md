API Endpoints (public, tanpa auth):

POST /api/v1/youtube/keyword-tracking/retry-all — retry semua keyword tracker aktif yang stuck/missed hari ini, langsung queue ke Celery
POST /api/v1/youtube/keyword-tracking/{tracker_id}/run — force run satu tracker tertentu sekarang (reset last_scraped_date dulu supaya tidak skip)
Dashboard scraping-status:

Tombol "▶ Retry Semua Keyword" di atas tabel keyword tracker (mirip tombol viral channel tracker)
Kolom "Aksi" baru di setiap baris: tombol "▶" per tracker untuk trigger satu tracker tertentu
Status feedback muncul di samping tombol (hijau = queued, merah = error)
Catatan: FC Barcelona tracker baru saja di-queue oleh retry-all. Jika EnsembleData masih expired (HTTP 493), task akan error lagi dan muncul di log. Setelah subscription diperpanjang, klik "▶ Retry Semua Keyword" dan tracker akan jalan langsung tanpa perlu tunggu 12:00 WIB.