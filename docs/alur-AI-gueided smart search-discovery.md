# Alur AI-Guided Smart Search Discovery ("Subsistem A2")

## Apa yang dibangun (alur kerja)

```
1. Kamu simpan topik + keyword di Smart Search, aktifkan "pemantauan berkala"
   (schedule_recurring = true) lewat POST /search/topics/{id}/schedule
        ↓
2. Tiap hari jam 08:00 WIB, sistem otomatis:
   - Ambil semua topik yang "pemantauan berkala"-nya aktif
   - Untuk tiap topik, cek: platform mana yang HARI INI belum ketemu akunnya?
     (kalau semua platform sudah ketemu, SKIP -- tidak buang biaya AI)
        ↓
3. Kalau ada yang belum ketemu, AI dipanggil (Claude, atau Ollama kalau
   Claude lagi bermasalah) dengan instruksi: "cari PERKEMBANGAN TERBARU
   terkait topik ini" -- bukan cari ulang keyword yang sama persis
        ↓
4. Hasil AI (sub-topik baru + akun Instagram/Facebook/TikTok/Twitter
   yang nyata) disimpan ke tabel trend_recommendations (status "pending")
        ↓
5. Pipeline scrape harian tiap platform (yang SUDAH ADA sebelumnya,
   jadwalnya beda-beda: Instagram 09:00, Facebook 10:00, dst) otomatis
   ambil topik "pending" itu dan scrape akunnya
```

## Cara kamu verifikasi sendiri

**Langkah 1 — aktifkan pemantauan berkala di salah satu topikmu:**
```
POST /search/topics/{topic_id}/schedule
{"enabled": true, "duration_days": 7}
```

**Langkah 2 — cek status/monitoring-nya kapan saja:**
```
GET /search/topics/ai-discovery/status
```
Ini akan menunjukkan:
- `config` — provider AI yang aktif, jam jadwal, batas budget
- `last_run` — kapan terakhir jalan, berhasil/tidak
- `topics` — topik mana yang dapat giliran AI hari itu, sub-topik BARU apa yang ditemukan, dan status scrape-nya (sudah diambil pipeline harian atau belum)

**Langkah 3 — tunggu jam 08:00 WIB besok** (atau minta trigger manual kalau mau lihat sekarang juga) — lalu cek lagi endpoint di atas, harusnya ada isinya.

**Yang perlu diketahui saat verifikasi:**
- Kalau saldo Anthropic masih habis (kondisi sekarang), sistem otomatis pakai Ollama — prosesnya bisa 5-10 menit sekali jalan (bukan instan), jadi jangan kaget kalau `GET status` masih kosong beberapa menit setelah jadwal jalan.
- Kalau topik itu SUDAH dapat giliran AI hari itu, panggilan berikutnya di hari yang sama otomatis di-skip (sudah dites: hasilnya instan, 0.14 detik, tanpa panggil AI lagi) — ini supaya tidak boros biaya.
- Kualitas hasil AI tergantung provider: waktu dites pakai Ollama, kadang hasilnya relevan (nemuin sudut berita baru soal insiden yang sama), kadang melenceng jauh dari topik — ini keterbatasan model Ollama yang sudah pernah dibahas sebelumnya, bukan bug di kode.
