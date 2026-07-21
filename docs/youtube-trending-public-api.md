# API Dashboard YouTube Trending — Publik (Tanpa Login)

Dokumentasi untuk tim frontend yang mau bikin dashboard trending YouTube yang bisa
**di-share via link ke siapa saja, tanpa perlu login** — mirip konsep "anyone with
the link" (Notion/Figma) atau share-link Maltego. Base URL: `https://api.dismi.xyz/api/v1`.

**TIDAK PERLU** `Authorization: Bearer <token>` — endpoint ini sengaja publik.

## Kenapa cukup 1 URL tetap (tidak ada sistem token per-share)

Data trending ini **global** — sama untuk semua orang, bukan dashboard personal
per-user/per-topik. Jadi "share dashboard" di sini sesederhana: frontend embed
endpoint ini di satu halaman, lalu URL HALAMAN itu (bukan URL API-nya langsung)
yang dibagikan ke orang lain. Tidak perlu backend generate token/link khusus per
share — siapa pun yang buka link frontend itu, browser mereka akan fetch endpoint
publik yang sama ini sendiri.

---

## `GET /youtube/trending-public` — trending 7 hari terakhir + video terpopuler

**Request:**
```
GET /youtube/trending-public?geo=ID
```

Query params:
| Param | Wajib? | Default | Keterangan |
|---|---|---|---|
| `geo` | tidak | `"ID"` | Kode negara Google Trends (ID/US/dll) |

**Response (contoh asli, hasil pengujian langsung ke API produksi 2026-07-16):**
```json
{
  "success": true,
  "data": {
    "geo": "ID",
    "days": [
      { "date": "2026-07-10", "topics": [ /* ... 10 topik ... */ ] },
      { "date": "2026-07-11", "topics": [ /* ... */ ] },
      { "date": "2026-07-12", "topics": [ /* ... */ ] },
      { "date": "2026-07-13", "topics": [ /* ... */ ] },
      { "date": "2026-07-14", "topics": [ /* ... */ ] },
      { "date": "2026-07-15", "topics": [ /* ... */ ] },
      {
        "date": "2026-07-16",
        "topics": [
          {
            "rank": 1,
            "title": "met",
            "traffic": "100+",
            "description": "",
            "fetched_at": "2026-07-16T05:00:02.064918+00:00",
            "video_count": 50,
            "top_videos": [
              {
                "title": "Speed Met Messi's Best Friend",
                "url": "https://www.youtube.com/watch?v=5oyfeCULodk",
                "channel": "RMS",
                "thumbnail": "https://i.ytimg.com/vi/5oyfeCULodk/hqdefault.jpg",
                "views": 0,
                "likes": 0,
                "published_at": "2026-07-13T11:10:39+00:00"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### Bentuk data

- `days` — **selalu tepat 7 entri**, urut dari 6 hari lalu s/d hari ini (`days[6]` = hari ini). Hari yang tidak punya topik trending sama sekali tetap muncul dengan `topics: []` — frontend tidak perlu isi celah tanggal sendiri.
- `topics` per hari — urut `rank` dari Google Trends (1 = paling trending), biasanya ~10 topik/hari.
- `video_count` — total video YouTube yang sudah berhasil dikumpulkan sistem untuk topik itu (bisa `0` kalau topik baru saja masuk trending dan belum sempat di-scrape — **topiknya tetap ditampilkan**, bukan disembunyikan, supaya list trending tidak "bolong").
- `top_videos` — maks **5 video**, urut `views` terbanyak dulu.

### Catatan penting — WAJIB dibaca sebelum render

- **`views`/`likes` bisa `0`** untuk video yang baru dikumpulkan lewat jalur fallback (EnsembleData sedang bermasalah → otomatis pindah ke YouTube Data API v3, yang butuh panggilan tambahan buat isi statistik dan kadang belum sempat jalan). Ini **bukan berarti video-nya genuinely 0 views** — kalau mau ditampilkan ke user, pertimbangkan sembunyikan angka `0` (tampilkan "-" atau skip) daripada nampilin "0 views" yang menyesatkan.
- Video di dalam satu topik **tidak selalu unik lintas hari** (video populer bisa nongol di top_videos beberapa hari berturut-turut kalau topiknya tetap trending) — kalau bikin galeri gabungan, dedup by `url`.
- `thumbnail` bisa `null`/kosong untuk sebagian video lama — siapkan placeholder.

### Caching & rate limit (perlu diketahui, bukan untuk di-handle frontend)

- Response di-cache 10 menit di server (Redis) — jangan kaget kalau data terasa "tidak real-time detik ini", memang sengaja supaya server tidak keberatan.
- Dibatasi **30 request/menit per IP**. Kalau lewat, dapat `HTTP 429` dengan header `Retry-After` (detik). Untuk pemakaian normal (load dashboard sesekali, bukan polling tiap detik) tidak akan kena.

---

## Contoh pemanggilan (pseudocode frontend)

```js
const res = await fetch("https://api.dismi.xyz/api/v1/youtube/trending-public?geo=ID");
const { data } = await res.json();

// data.days[6] = hari ini, data.days[0] = 6 hari lalu
const today = data.days[data.days.length - 1];
console.log(`Trending hari ini (${today.date}):`, today.topics.map(t => t.title));
```

Tidak perlu header `Authorization` sama sekali — cukup `fetch()` polos, bisa dipanggil dari domain manapun (CORS sudah dibuka untuk semua origin).
