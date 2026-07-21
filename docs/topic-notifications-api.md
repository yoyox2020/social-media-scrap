# API Notifikasi Topik Viral

Dokumentasi untuk tim frontend — fitur notifikasi otomatis: sistem cek **tiap jam**
apakah ada post baru (di antara semua topik Smart Search yang tersimpan) yang
melewati ambang batas "viral" per platform. Base URL: `https://api.dismi.xyz/api/v1`.

Semua endpoint di bawah **perlu login** (`Authorization: Bearer <token>`, dari
`POST /auth/login`) — beda dari `/youtube/trending-public` yang publik.

---

## Cara kerja singkat (perlu dipahami sebelum integrasi)

1. Tiap jam (Celery Beat, `menit ke-0`), sistem cek semua topik Smart Search aktif
2. Per topik → per platform → per keyword: cari post yang metriknya (views/likes,
   tergantung platform) melewati ambang batas **DAN masih dalam 30 hari terakhir**
   (dihitung dari `published_at` post, fallback ke `collected_at` kalau
   `published_at` kosong) — post viral yang sudah lebih dari sebulan tidak lagi
   dianggap "baru viral", jadi tidak dinotifikasi lagi
3. Kalau ketemu DAN post itu belum pernah dinotif sebelumnya → satu baris
   notifikasi baru dibuat
4. Frontend **polling** endpoint di bawah — TIDAK ada push/websocket, jadi
   frontend yang aktif nanya "ada yang baru?" secara berkala

**Ambang batas default per platform** (bisa diubah kapan saja lewat API, lihat endpoint 4 & 5):

| Platform | Metrik | Default |
|---|---|---|
| YouTube | views | 1.000.000 |
| TikTok | views | 500.000 |
| Twitter | likes | 10.000 |
| Facebook | likes | 5.000 *(tidak ada data views sama sekali)* |
| Instagram | likes | 5.000 *(tidak ada data views sama sekali)* |

**Ringkasan 7 endpoint** (semua perlu login, base URL `/api/v1/search`):

| # | Endpoint | Dipakai buat |
|---|---|---|
| 1 | `GET /notifications/unread-count` | Badge lonceng, poll sering (murah) |
| 2 | `GET /notifications` | Daftar lengkap, saat panel dibuka |
| 3 | `POST /notifications/{id}/read` | Tandai satu notifikasi dibaca |
| 4 | `GET /notifications/thresholds` | Lihat ambang batas viral tiap platform |
| 5 | `PATCH /notifications/thresholds` | Ubah ambang batas satu platform |
| 6 | `GET /notifications/lookback-days` | Lihat jendela waktu "masih trending" |
| 7 | `PATCH /notifications/lookback-days` | Ubah jendela waktu tsb |

Endpoint 1-3 dipakai SEMUA frontend yang pasang lonceng notifikasi. Endpoint 4-7
opsional, cuma perlu kalau ada halaman admin/pengaturan.

---

## 1. `GET /search/notifications/unread-count` — badge notifikasi

Paling murah, cocok di-poll paling sering (mis. tiap 15-30 detik) buat nampilin badge angka merah di icon lonceng.

**Request:**
```
GET /search/notifications/unread-count
GET /search/notifications/unread-count?topic_id=<uuid>   # opsional, badge per-topik
```

**Response (contoh live, 2026-07-17):**
```json
{ "success": true, "data": { "unread_count": 80 } }
```

---

## 2. `GET /search/notifications` — daftar lengkap

Dipanggil saat user KLIK icon lonceng (buka dropdown/panel notifikasi).

**Request:**
```
GET /search/notifications?limit=20&page=1
GET /search/notifications?topic_id=<uuid>          # filter satu topik
GET /search/notifications?platform=youtube          # filter satu platform
GET /search/notifications?is_read=false              # cuma yang belum dibaca
```

**Response (contoh live dari produksi, diambil 2026-07-17 setelah filter 30 hari aktif):**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "91c96149-6aa4-4a81-b120-ce46a213649a",
        "topic_id": "3237c49d-c72e-4629-9013-157bf2b18a79",
        "platform": "youtube",
        "post_id": "6f3310dc-81cd-4411-bb61-de81e5b9fb53",
        "keyword_text": "jampidsus",
        "metric_type": "views",
        "metric_value": 1915114,
        "threshold": 1000000,
        "title": "Ada apa? Puluhan prajurit TNI berjaga di sekitar rumah Jampidsus, Febrie Adriansyah",
        "author": "Delegasi TV",
        "url": "https://www.youtube.com/watch?v=G1fNVyrM7yI",
        "is_read": false,
        "created_at": "2026-07-17T07:41:36.898544+00:00"
      }
    ],
    "pagination": { "page": 1, "limit": 20, "total": 82, "total_pages": 5 }
  }
}
```

**Field yang dipakai untuk render kartu notifikasi:**
- `title` + `author` → isi utama kartu ("Video X oleh Y sedang viral")
- `metric_value` vs `threshold` → mis. tampilkan "1.052.423 views (ambang: 1jt)"
- `platform` → buat icon platform
- `url` → link "buka post" (klik langsung ke YouTube/dll)
- `keyword_text` → info tambahan "terkait keyword: ..."

---

## 3. `POST /search/notifications/{id}/read` — tandai dibaca

Dipanggil saat user klik/buka satu notifikasi.

**Request:** `POST /search/notifications/646ea89c-7827-427e-9405-35f371436e6c/read` (tanpa body)

**Response:** `{ "success": true, "data": { "id": "...", "is_read": true } }`

Setelah ini, panggil ulang `unread-count` (atau kurangi manual di state frontend) buat update badge.

---

## 4. `GET /search/notifications/thresholds` — lihat ambang batas viral (opsional, buat halaman admin/pengaturan)

```json
{ "success": true, "data": { "thresholds": {
  "youtube": {"metric": "views", "value": 1000000},
  "tiktok": {"metric": "views", "value": 500000},
  "twitter": {"metric": "likes", "value": 10000},
  "facebook": {"metric": "likes", "value": 5000},
  "instagram": {"metric": "likes", "value": 5000}
}}}
```

---

## 5. `PATCH /search/notifications/thresholds` — ubah ambang batas satu platform

**Request:**
```
PATCH /search/notifications/thresholds
Content-Type: application/json

{ "platform": "twitter", "metric": "likes", "value": 15000 }
```
`metric` harus `"views"` atau `"likes"`. Perubahan **langsung aktif** di pengecekan jam berikutnya, tidak perlu tunggu deploy apa pun.

---

## 6. `GET /search/notifications/lookback-days` — lihat jendela waktu "masih dianggap trending" (opsional, buat halaman admin/pengaturan)

Default 30 hari (lihat bagian "Cara kerja singkat" di atas) — **bisa diubah dari frontend**,
sama seperti ambang batas, tanpa restart/deploy apa pun.

```json
{ "success": true, "data": { "lookback_days": 30 } }
```

---

## 7. `PATCH /search/notifications/lookback-days` — ubah jendela waktu

**Request:**
```
PATCH /search/notifications/lookback-days
Content-Type: application/json

{ "days": 14 }
```
`days` harus bilangan bulat positif (> 0), kalau tidak API balas `422`. Perubahan **langsung
aktif** di pengecekan jam berikutnya. Contoh: kalau diubah ke `7`, post yang published lebih
dari 7 hari lalu berhenti dianggap "baru viral" dan tidak dinotifikasi lagi (notifikasi yang
SUDAH ada di DB tidak otomatis terhapus, cuma mempengaruhi deteksi ke depan).

---

## Contoh alur frontend (pseudocode)

```js
// Poll badge tiap 20 detik
setInterval(async () => {
  const { data } = await fetch("/api/v1/search/notifications/unread-count", { headers }).then(r => r.json());
  updateBellBadge(data.unread_count);
}, 20000);

// Saat user buka dropdown notifikasi
async function openNotificationPanel() {
  const { data } = await fetch("/api/v1/search/notifications?limit=20", { headers }).then(r => r.json());
  renderList(data.items);
}

// Saat user klik satu notifikasi
async function onNotificationClick(notif) {
  await fetch(`/api/v1/search/notifications/${notif.id}/read`, { method: "POST", headers });
  window.open(notif.url, "_blank");
  refreshBadge();
}
```

---

## Checklist verifikasi (cek ini sebelum anggap integrasi selesai)

Semua endpoint di atas sudah dites live di produksi (bukan cuma di dokumentasi), tapi
tim frontend tetap perlu verifikasi sisi UI-nya cocok dengan perilaku berikut:

- [ ] **Badge angka** cocok dgn `unread_count` dari API — jangan hitung ulang di
      frontend dari `list`, badge itu query terpisah yg lebih murah (lihat endpoint 1)
- [ ] **Notifikasi lebih tua dari `lookback_days` TIDAK PERNAH muncul** (default 30
      hari, bisa diubah lewat endpoint 6), baik yg baru atau yg lama — sistem sudah
      filter di query backend (`published_at`/`collected_at`), bukan sesuatu yg perlu
      difilter ulang di frontend
- [ ] **Ubah jendela waktu** (`PATCH .../lookback-days`) — sama seperti threshold,
      efeknya baru kelihatan di pengecekan jam BERIKUTNYA, bukan retroaktif ke
      notifikasi yg sudah ada
- [ ] **Klik notifikasi → badge berkurang** setelah `POST .../read`, tanpa perlu
      reload halaman (panggil ulang `unread-count` atau kurangi state lokal)
- [ ] **Platform tanpa `views`** (Facebook & Instagram) — jangan tampilkan "0 views",
      field `metric_type` untuk keduanya SELALU `"likes"`, render sesuai `metric_type`
      yg dikirim, bukan diasumsikan
- [ ] **Ubah threshold di halaman admin/pengaturan** (`PATCH .../thresholds`) → tidak
      butuh reload/redeploy apa pun, efeknya baru kelihatan di notifikasi BERIKUTNYA
      (jalan tiap jam di menit ke-0), bukan instan ke notifikasi yg sudah ada
- [ ] **Notifikasi tidak pernah dobel** untuk post yg sama di topik yg sama — kalau
      lihat duplikat di UI, itu bug di sisi frontend (mis. state tidak di-dedup saat
      polling), bukan di backend (constraint DB mencegah ini)
- [ ] **`url` bisa null** kalau post tidak punya link tersimpan — pastikan tombol
      "buka post" disembunyikan/disabled saat `url === null`, jangan `window.open(null)`

**Data live utk uji manual** (per 2026-07-17): 82 notifikasi total, 80 belum dibaca,
5 platform aktif (`youtube` paling banyak). Kalau butuh token test: lihat kredensial
`testing@redteam.id` di tim backend — JANGAN pakai akun ini utk data produksi asli,
khusus testing endpoint.
