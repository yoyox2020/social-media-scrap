# Fitur Viral Tracking Intelligence

Dokumentasi lengkap ada di: [09.VIRAL-TRACKING.md](./09.VIRAL-TRACKING.md)

---

## Ringkasan Singkat

Sistem ini mendeteksi video YouTube ≥1 juta views, melacak channel pemiliknya selama 7 hari (5 video/hari), lalu menganalisis akun yang berkomentar berulang sebagai indikator bot/buzzer.

### Cara Kerja

```
Post ≥1M views masuk DB
        │
        ▼
detect_viral_posts (tiap 6 jam)
  → 1 tracker per channel baru
        │
        ▼ (setiap hari, auto)
channel_daily_scrape(tracker_id)
  → 5 video terbaru dari channel
  → simpan ke posts (metadata: tracker_id, source=viral_tracking)
  → log: {day, date, posts_new, posts_skipped}
        │
        ▼
check_flagged_commenters(tracker_id)
  → cari akun komentar >10x
  → flag → buat tracker baru untuk akun tersebut (rekursif)
```

### Trigger

| Trigger | Kapan | Aksi |
|---------|-------|------|
| Otomatis | Setiap 6 jam | Scan DB cari channel baru ≥1M views |
| Otomatis | Setiap hari 03:00 | Resume tracker aktif yang belum scraping |
| Otomatis | Setelah tiap scrape | Cek commenter mencurigakan |
| Manual | `POST /viral-tracking/detect` | Force deteksi sekarang |
| Manual | `POST /viral-tracking/{id}/scrape` | Force scrape 1 tracker |

### Endpoints Utama

```
GET  /viral-tracking                    ← list semua tracker
GET  /viral-tracking/{tracker_id}       ← detail + 7-day timeline
POST /viral-tracking/detect             ← trigger deteksi manual
POST /viral-tracking/{id}/scrape        ← trigger scrape manual
GET  /flagged-accounts                  ← akun mencurigakan
```
