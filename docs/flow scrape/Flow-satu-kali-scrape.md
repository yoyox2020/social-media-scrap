
Flow satu kali scrape (1 akun)
Trigger (POST /instagram/scrape atau batch trend-scrape)
        │
        ▼
Cek dulu: akun ini sudah discrape hari ini?
        │
        ├─ SUDAH → langsung selesai (<0.1 detik) — tidak panggil Apify sama sekali
        │
        └─ BELUM → lanjut ke Apify (di sinilah waktu terbuang)
                │
                ▼
        [1] Apify menyalakan container baru untuk Actor
            "Pulling container image" → "Creating container" → "Starting container"
            ~5-10 detik — OVERHEAD TETAP, terjadi di SETIAP scrape baru
                │
                ▼
        [2] "Checking for profiles on social networks..."
            ~1-2 detik
                │
                ▼
        [3] Sub-actor apify/instagram-scraper: "Getting profile info"
            ~3-5 detik
                │
                ▼
        [4] Sub-actor apify/instagram-scraper: "Getting posts"
            ~5-15 detik (tergantung akun, makin besar/aktif akunnya makin lama)
                │
                ▼
        [5] Sub-actor TERPISAH apify/instagram-comment-scraper: "Getting comments"
            ~10-40 detik — INI BIASANYA PALING LAMA, actor kedua yang beda dari
            actor posts, jalan setelah actor posts selesai (berurutan, bukan paralel)
                │
                ▼
        Total: 20-80 detik untuk 1 akun (contoh nyata dari log sesi ini:
        natgeo ~22s, tesla ~35s, spacex ~38s, coldplay ~63s, bahlil ~80s)



Kenapa batch (5 topik/hari) bisa lebih lama lagi
Prosesnya berurutan (satu-satu), bukan paralel — topik ke-2 baru mulai setelah topik ke-1 selesai total. Jadi kalau 5 topik semuanya akun BARU (belum pernah discrape), totalnya bisa 5 × 20-80 detik = 1,5 - 6+ menit untuk satu batch.

Satu-satunya yang mempercepat: dedup akun-per-hari yang baru kita bangun. Kalau beberapa topik mengarah ke akun yang SAMA (misal beberapa topik "Coldplay" semua ke @coldplay), begitu akun itu selesai discrape sekali, topik berikutnya yang akun-nya sama selesai dalam <0.1 detik — terbukti nyata di log: batch 5 topik tadi selesai cuma 0,45 detik total karena semuanya kebetulan share 2 akun yang sudah pernah discrape.

Ringkasan penyebab utama "lama"
Bukan bug di kode kita — ini karakteristik Apify Actor itu sendiri: setiap run menyalakan container baru dari nol (bukan proses yang sudah "panas"/standby), dan comment-scraper adalah actor kedua yang terpisah, jalan setelahnya.
Berurutan, bukan paralel — kalau budget harian (5) semuanya akun baru, waktunya berkali lipat.
Yang sudah dipercepat: akun yang berulang di hari yang sama (dedup), sudah kita buktikan bekerja.