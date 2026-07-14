# API untuk Dashboard Engagement

Dokumentasi ini untuk tim frontend yang mau bikin ulang dashboard engagement
(simulasi: kartu ringkasan per platform, grafik komposisi engagement, grafik
tren mention harian). Base URL: `https://api.dismi.xyz/api/v1`. Header wajib
di semua request: `Authorization: Bearer <token>` (dapat dari `POST /auth/login`).

Semua contoh di bawah **hasil pengujian langsung** ke API produksi (bukan
contoh dikarang) — request dan response persis apa adanya, periode
1 Jun – 15 Jul 2026.

---

## Ringkasan kebutuhan

Dashboard butuh **2 endpoint**, dipanggil **per platform** (bukan sekali
gabungan) karena kartu ringkasan & grafik komposisi perlu breakdown
masing-masing platform sendiri-sendiri:

| Komponen UI | Endpoint | Dipanggil |
|---|---|---|
| Kartu ringkasan (Engagement, Mentions, Reach, Exposure, Sentimen) | `GET /metrics/summary` | 1x per platform |
| Grafik komposisi Engagement (Likes/Komentar/Share/Save) | `GET /metrics/summary` | (field `breakdown`, sama request di atas) |
| Grafik tren mention harian | `GET /metrics/trend` | 1x per platform |

Platform yang didukung: `youtube`, `tiktok`, `twitter`, `facebook`, `instagram`.
Untuk dashboard 5-platform seperti simulasi kemarin = **10 request total**
(5 platform × 2 endpoint).

---

## 1. `GET /metrics/summary` — kartu ringkasan + komposisi engagement

**Request:**
```
GET /metrics/summary?platforms=tiktok&date_from=2026-06-01T00:00:00Z&date_to=2026-07-15T00:00:00Z
Authorization: Bearer <token>
```

Query params:
| Param | Wajib? | Default | Keterangan |
|---|---|---|---|
| `platforms` | tidak | `["youtube"]` | Bisa diulang (`?platforms=a&platforms=b`) untuk gabungan; untuk dashboard per-platform kirim satu-satu |
| `date_from` / `date_to` | tidak | 30 hari terakhir | ISO 8601, UTC |
| `include_growth` | tidak | `true` | Hitung `mention_growth` vs periode sebelumnya (durasi sama) |

**Response (contoh asli, `platforms=tiktok`):**
```json
{
  "success": true,
  "data": {
    "scope": "global",
    "platforms": ["tiktok"],
    "period": { "from": "2026-06-01T00:00:00+00:00", "to": "2026-07-15T00:00:00+00:00" },
    "metrics": {
      "exposure": { "value": 1156958, "label": "Total Impression", "description": "Total tayangan seluruh postingan" },
      "reach": { "value": 4, "label": "Reach", "description": "Total akun unik (channel/creator) yang membahas topik ini" },
      "engagement": {
        "value": 54394,
        "label": "Engagement",
        "description": "Total Like + Komentar + Share + Save + Reply + Klik",
        "breakdown": { "likes": 49153, "comments": 70, "shares": 2536, "saves": 2635, "replies": 0, "clicks": 0 }
      },
      "engagement_rate": { "value": 1359850.0, "unit": "%", "label": "Engagement Rate" },
      "sentiment_score": {
        "value": 31.43, "unit": "%", "label": "Sentiment Score",
        "detail": { "positif": 22, "negatif": 0, "netral": 48, "total": 70 }
      },
      "sov": { "value": null, "available": false, "label": "Share of Voice" },
      "mention_growth": { "value": 333.33, "unit": "%", "available": true },
      "mentions": { "value": 13, "label": "Total Mentions" }
    }
  }
}
```

**Field yang dipakai di dashboard:**
- Kartu "Total Engagement" (hero number) → `metrics.engagement.value`
- Mini-bar & grafik komposisi (Likes/Komentar/Share/Save) → `metrics.engagement.breakdown.{likes,comments,shares,saves}`
- Baris "Mentions" / "Reach" / "Exposure" → `metrics.mentions.value` / `metrics.reach.value` / `metrics.exposure.value`
- Chip Sentimen → `metrics.sentiment_score.value` (persen; `> 0` = kecenderungan positif, `< 0` = negatif)

**Catatan penting:**
- `replies` dan `clicks` **selalu 0 di semua platform saat ini** — tidak ada provider yang menyediakan data ini. Boleh disembunyikan dari legend kalau mau, tapi field-nya tetap ada di response.
- `exposure` = **0 untuk Facebook & Instagram** — ini **bukan bug**, provider (Apify) tidak pernah mengirim data tayangan/views utk 2 platform ini. Tampilkan sebagai "Tidak tersedia" kalau mau lebih jelas ke user, jangan diasumsikan data hilang.
- `shares` juga selalu 0 untuk YouTube, Facebook, Instagram (provider tidak sediakan); TikTok & Twitter yang punya data share asli.
- `sov` cuma terisi kalau panggil endpoint `/metrics/keyword/{id}` atau `/metrics/topic/{id}` (butuh pembanding keyword lain) — di `/summary` global selalu `null`.

---

## 2. `GET /metrics/trend` — grafik tren mention harian

**Request:**
```
GET /metrics/trend?platforms=tiktok&granularity=day&date_from=2026-06-01T00:00:00Z&date_to=2026-07-15T00:00:00Z
Authorization: Bearer <token>
```

Query params:
| Param | Wajib? | Default | Keterangan |
|---|---|---|---|
| `platforms` | tidak | `["youtube"]` | **Kalau kirim >1 platform sekaligus, hasilnya DIGABUNG per tanggal** (bukan pecah per platform) — utk grafik multi-garis (satu garis per platform), panggil terpisah per platform |
| `keyword_ids` / `topic_id` | tidak | — | Filter ke keyword/topik tertentu (kosongkan = semua) |
| `granularity` | tidak | `"day"` | `day` / `week` / `month` |

**Response (contoh asli, `platforms=tiktok`):**
```json
{
  "success": true,
  "data": {
    "scope": "trend",
    "granularity": "day",
    "platforms": ["tiktok"],
    "period": { "from": "2026-06-01T00:00:00+00:00", "to": "2026-07-15T00:00:00+00:00" },
    "series": [
      { "period": "2026-06-17T00:00:00+00:00", "mentions": 1 },
      { "period": "2026-07-04T00:00:00+00:00", "mentions": 1 },
      { "period": "2026-07-05T00:00:00+00:00", "mentions": 1 },
      { "period": "2026-07-06T00:00:00+00:00", "mentions": 2 },
      { "period": "2026-07-08T00:00:00+00:00", "mentions": 4 },
      { "period": "2026-07-09T00:00:00+00:00", "mentions": 8 }
    ]
  }
}
```

**Catatan penting — WAJIB dibaca sebelum render grafik:**
- **Tanggal yang mention-nya 0 TIDAK muncul di array** (lihat contoh: 1 Juni s/d 16 Juni kosong sama sekali untuk TikTok). Frontend **harus bikin sendiri deret tanggal lengkap** dari `date_from` s/d `date_to` dan isi `0` untuk tanggal yang tidak ada di `series`, supaya garis grafik tetap nyambung dan tidak melompat-lompat.
- Skala tiap platform bisa beda jauh (YouTube bisa ratusan mention/hari, TikTok cuma satuan) — kalau digambar di satu sumbu-Y yang sama, garis platform kecil akan kelihatan rata/hilang. Simulasi kemarin pakai **panel terpisah per platform, sumbu-Y masing-masing sendiri** untuk ini.

---

## Contoh alur pemanggilan (pseudocode frontend)

```js
const platforms = ["youtube", "tiktok", "twitter", "facebook", "instagram"];
const params = "date_from=2026-06-01T00:00:00Z&date_to=2026-07-15T00:00:00Z";

const summaries = await Promise.all(
  platforms.map(p => fetch(`/api/v1/metrics/summary?platforms=${p}&${params}`, { headers }).then(r => r.json()))
);
const trends = await Promise.all(
  platforms.map(p => fetch(`/api/v1/metrics/trend?platforms=${p}&granularity=day&${params}`, { headers }).then(r => r.json()))
);
```

## Auth

```
POST /auth/login
{ "username": "<email atau username>", "password": "<password>" }
```
Response berisi `access_token` — pakai sebagai `Authorization: Bearer <access_token>` di semua request di atas. Token expire sesuai `jwt_access_token_expire_minutes` (kalau 401, login ulang).
