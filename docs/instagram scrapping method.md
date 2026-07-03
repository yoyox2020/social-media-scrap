Sekarang sudah jelas gambar lengkapnya. Ini penjelasan sistematis:

Cara trigger scraping Instagram — ada 3 jalur
Jalur 1 — Otomatis: Celery Beat jam 09:00 WIB

Celery Beat → instagram_trending_daily_task
               ↓
           run_daily_trending()
               ├── run_discovery()  : cari akun trending via EnsembleData hashtag
               ├── run_scoring()    : hitung trending_score dari data di DB
               └── run_scrape_account() × 5 : scrape 2 post + 5 comment per akun top 5
Ini jalan sendiri setiap hari jam 09:00, tapi butuh EnsembleData subscription aktif.



Jalur 2 — Manual via API: GET /instagram/posts

GET /instagram/posts?username=tukang_jelajah&max_comments=5
Dipanggil user/frontend untuk username tertentu
Otomatis scrape jika belum pernah ada data atau force_refresh=true dan belum scrape hari ini
Batas: 5 post per username per hari
Ini yang paling mudah di-trigger sekarang



Jalur 3 — On-demand Celery: instagram_trending_scrape_account_task

instagram_trending_scrape_account_task.delay(account_id="<UUID>")
Hanya bisa dipanggil langsung dari kode/worker — belum ada endpoint API untuk trigger ini.


Kedua task masuk ke Celery worker dan dieksekusi di background
workers.instagram.scrape_username selesai dalam 5 detik
workers.instagram_trending.scrape_account selesai dalam 4.9 detik, dan sudah update last_scraped_date + scrape_logs di tabel trending
Error 493 = EnsembleData subscription expired — bukan bug, memang menunggu renewal
Saat subscription EnsembleData aktif kembali, kedua endpoint ini langsung jalan tanpa perubahan apapun — tinggal panggil POST /instagram/scrape?username=<username> dan scraping akan berjalan di background.


Ringkasan sistem sekarang
POST vs GET:

POST /instagram/scrape → trigger Celery worker background → scrape EnsembleData → simpan posts + comments + sentimen ke DB
GET /instagram/posts → cek DB dulu, scrape hanya jika kosong atau force_refresh=true
GET /instagram/comments → baca DB saja, tidak scrape
Sumber data trending:

run_discovery() → EnsembleData search hashtag #indonesia, #viral, #fyp, #trending → dapat daftar username aktif
Hasilnya masuk instagram_trending_accounts → di-score → top 5 di-scrape 2 post + 5 comment
Monitoring EnsembleData "waiting" state — sekarang otomatis:

Dashboard baca riwayat error dari scrape_runs dan instagram_trending_accounts.scrape_logs
Jika ada error 493 dalam 48 jam → banner EXPIRED / MENUNGGU RENEWAL berwarna merah
Jika tidak → AKTIF berwarna hijau
Menampilkan kapan error terakhir (2026-07-03) dan sukses terakhir (2026-07-02)
Recovery otomatis:

Saat subscription diperbarui, Celery Beat langsung jalan di jadwal berikutnya tanpa perlu trigger manual:
Instagram: jam 09:00 WIB
YouTube trending: jam 12:00 WIB
Atau bisa trigger manual: POST /instagram/scrape?username=...
Dashboard: http://187.77.125.10:8000/scraping-status — sudah tampil section Instagram Trending + banner EnsembleData status, auto-refresh 15 detik.