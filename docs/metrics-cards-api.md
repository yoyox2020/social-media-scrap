# Metrics Cards API — Panduan Frontend (sesuai tampilan kartu per-platform)

Dokumen ini fokus HANYA pada satu hal: cara memanggil data supaya persis
menghasilkan tampilan kartu per-platform (YouTube/TikTok/Twitter/Facebook/
Instagram, masing-masing dengan Total Engagement, Mentions, Reach,
Exposure, Sentimen). Drill-down (klik salah satu angka untuk lihat data
mentah di baliknya) ada di dokumen TERPISAH:
[metrics-engagement-api.md](metrics-engagement-api.md) — belum perlu
dibaca sekarang, selesaikan kartu ini dulu.

Router: [app/api/v1/metrics.py](../app/api/v1/metrics.py)

Butuh login (`Authorization: Bearer <token>`).

---

## 1 kartu = 1 kali panggil endpoint

```
GET /api/v1/metrics/keyword/{keyword_id}?platforms={platform}
```

**Panggil endpoint ini 5 KALI** (sekali per platform) untuk hasilkan 5
kartu di gambar — BUKAN sekali dengan 5 platform sekaligus, karena kalau
`platforms` diisi lebih dari satu, angkanya akan DIGABUNG jadi satu kartu
raksasa, bukan 5 kartu terpisah.

```
GET /api/v1/metrics/keyword/3237c49d-...?platforms=youtube      ← kartu YouTube
GET /api/v1/metrics/keyword/3237c49d-...?platforms=tiktok       ← kartu TikTok
GET /api/v1/metrics/keyword/3237c49d-...?platforms=twitter      ← kartu Twitter
GET /api/v1/metrics/keyword/3237c49d-...?platforms=facebook     ← kartu Facebook
GET /api/v1/metrics/keyword/3237c49d-...?platforms=instagram    ← kartu Instagram
```

Param lain yang berguna:
- `date_from` / `date_to` — rentang tanggal. Kosong = default 30 hari terakhir.
- `include_growth` — default `true`, hitung juga `mention_growth` (tren naik/turun).

---

## Mapping tiap elemen visual → field response

Contoh response utk `platforms=youtube`:

```json
{
  "success": true,
  "data": {
    "scope": "keyword",
    "keyword": { "id": "3237c49d-...", "text": "anies" },
    "platforms": ["youtube"],
    "period": { "from": "2026-06-18T00:00:00+00:00", "to": "2026-07-18T00:00:00+00:00" },
    "metrics": {
      "engagement": {
        "value": 21650000,
        "label": "Engagement",
        "breakdown": { "likes": 15000000, "comments": 4800000, "shares": 1850000, "saves": 0, "replies": 0, "clicks": 0 }
      },
      "mentions": { "value": 4862, "label": "Total Mentions" },
      "reach": { "value": 2669, "label": "Reach" },
      "exposure": { "value": 1358140000, "label": "Total Impression" },
      "sentiment_score": {
        "value": 11.3,
        "unit": "%",
        "detail": { "positif": 1200, "negatif": 300, "netral": 900, "total": 2400 }
      },
      "per_platform": {
        "youtube": {
          "mentions": 4862, "exposure": 1358140000, "reach": 2669, "engagement": 21650000,
          "engagement_breakdown": {
            "likes": 15000000, "comments": 4800000, "shares": 1850000, "saves": 0, "replies": 0, "clicks": 0,
            "unavailable_fields": ["shares", "saves", "replies", "clicks"]
          }
        }
      }
    }
  }
}
```

| Elemen di kartu | Ambil dari field | Catatan |
|---|---|---|
| Ikon + nama platform (YouTube/TikTok/dst) | — (statis di frontend) | dari `platforms=` yang kamu kirim sendiri, bukan dari response |
| **Total Engagement** (angka besar) | `metrics.engagement.value` | jumlah likes+komentar+share+save+reply+klik, lihat `metrics.engagement.breakdown` kalau mau rincian |
| Progress bar di bawah Total Engagement | — (hitung sendiri di frontend) | API tidak kirim "persentase bar" — kalau mau bar perbandingan antar-platform, hitung `engagement platform ini ÷ engagement platform tertinggi × 100%` di frontend |
| **Mentions** | `metrics.mentions.value` | jumlah post yang menyebut keyword ini di platform ini |
| **Reach** | `metrics.reach.value` | jumlah akun/channel UNIK yang membahas (bukan jumlah post) |
| **Exposure** | `metrics.exposure.value` **ATAU** teks "Tidak tersedia" | lihat aturan di bawah — JANGAN cuma cek `=== 0` |
| **Sentimen** (angka % + panah) | `metrics.sentiment_score.value` | lihat aturan panah di bawah |

---

## Aturan Exposure: kapan tampilkan "Tidak tersedia"

Facebook & Instagram **memang tidak pernah** dapat data views dari
provider scraping (fakta permanen, bukan bug) — API tetap balikin
`exposure.value = 0` (angka numerik selalu ada, konsisten), TAPI frontend
harus tampilkan **"Tidak tersedia"** bukan **"0"** untuk 2 platform itu, biar
tidak disalahartikan "postingannya benar-benar 0 ditonton".

Cara PALING BENAR (tidak hardcode nama platform di frontend, otomatis
ikut kalau suatu saat providernya berubah):

```js
const pf = data.metrics.per_platform[platform]; // mis. platform = "youtube"
const exposureAvailable = !pf.engagement_breakdown.unavailable_fields.includes("views");

exposureText = exposureAvailable
  ? formatNumber(data.metrics.exposure.value)
  : "Tidak tersedia";
```

Kalau mau jalan pintas cepat (tidak akurat kalau providernya berubah nanti):
`platform === "facebook" || platform === "instagram" ? "Tidak tersedia" : ...`

---

## Aturan Sentimen: panah naik/turun/datar

`sentiment_score.value` sudah berupa angka bertanda (`+11.3`, `-5.2`,
`0.0`, dst) — hasil rumus `((Positif − Negatif) ÷ Total) × 100`. Aturan
panah (sesuai gambar: YouTube/TikTok/Facebook/Instagram panah hijau ke
atas, Twitter titik netral):

```js
if (value > 0)      → panah HIJAU ke atas,  teks "+{value}%"
else if (value < 0)  → panah MERAH ke bawah, teks "{value}%" (sudah minus otomatis)
else                  → titik netral,         teks "0.0%"
```

Kalau mau tampilkan rincian (hover/tooltip), pakai
`sentiment_score.detail` — `{positif, negatif, netral, total}` jumlah
komentar mentah di balik persentase itu.

---

## Contoh kode ambil 5 kartu sekaligus

```js
const PLATFORMS = ["youtube", "tiktok", "twitter", "facebook", "instagram"];

async function loadCards(keywordId) {
  const cards = await Promise.all(
    PLATFORMS.map(async (platform) => {
      const res = await fetch(
        `/api/v1/metrics/keyword/${keywordId}?platforms=${platform}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      const json = await res.json();
      return { platform, metrics: json.data.metrics };
    })
  );
  return cards; // render 5 kartu dari array ini
}
```

---

## Error yang mungkin muncul

| Kasus | Response |
|---|---|
| `keyword_id` tidak ada di DB | `404` — `"Keyword {id} tidak ditemukan"` |
| Tanpa token / token invalid | `401` |
| `platforms` diisi nama platform yang tidak dikenal (typo) | Tidak error — angkanya cuma jadi 0 semua (lihat catatan `get_adapter()` di [metrics-engagement-api.md](metrics-engagement-api.md) kalau butuh debug kenapa 0) |
