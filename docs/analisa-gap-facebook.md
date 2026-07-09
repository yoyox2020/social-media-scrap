# Analisa Gap Facebook (Subsistem B: scrape via Apify)

Tanggal: 2026-07-09. Dibuat dari sesi analisa kode setelah `docs/alur-search-facebook.md`.
Konteks: user menyampaikan pemahamannya soal alur Facebook (auto trending scrape +
keyword search + kuota habis), lalu diminta dikonfirmasi terhadap kode asli.

Pernyataan user yang dikonfirmasi:
> "facebook sudah auto trending scrap, jika cari keyword cari dulu di database
> kalau tidak ketemu simpan keyword lalu cari di apify, jika kuota habis
> jadwalkan dan pending"

---

## 1. Yang BENAR dari pernyataan user

1. **"Sudah auto trending scrape"** — Celery Beat jalan tiap hari jam `10:00 WIB`
   (`settings.facebook_trend_scrape_schedule_hour/minute`), ambil maks **5
   topik/hari** (`settings.facebook_trend_daily_budget`) dari
   `trend_recommendations` yang `status='pending'` + punya akun Facebook, urut
   score tertinggi.
   Sumber: `app/services/facebook/trend_scrape_service.py:42-58`,
   `app/workers/celery_app.py:101-109`.
2. **"Cari keyword, cari dulu di database"** — Tingkat 1 di
   `GET /facebook/posts/search`: `posts.content ILIKE` / hashtag exact di DB
   lokal, tidak panggil Apify sama sekali kalau ketemu.
   Sumber: `app/api/v1/facebook/router.py:337-373`.
3. **"Kalau tidak ketemu, cari di Apify"** — benar, tapi lewat 2 sub-tingkat:
   dulu cek topik yang sudah ada di `trend_recommendations` (tingkat 2), baru
   kalau itu juga nihil, search Apify beneran ke Facebook (tingkat 3).
   Sumber: `app/api/v1/facebook/router.py:385-432`.

## 2. Yang perlu DIKOREKSI

1. **Urutan "simpan keyword lalu cari di Apify" itu terbalik.**
   `discover_facebook_topic_by_keyword()` (tingkat 3) sebenarnya **search
   Apify DULU**, baru hasil akunnya **disimpan** ke `trend_recommendations`
   (`status='pending'`, `source='manual_facebook_search'`).
   Sumber: `app/services/facebook/trend_scrape_service.py:246-307`.
2. **Ada scrape langsung + tetap pending sekaligus (bukan cuma "disimpan").**
   Khusus dipanggil dari `/posts/search`, setelah disimpan sebagai pending,
   endpoint ini **langsung juga scrape SEKARANG JUGA**
   (`_scrape_now_and_respond`, `source_label="scraped_now_external"`) — tidak
   nunggu antrian budget besok. Tapi karena `mark_topic=None` di jalur ini,
   topik itu **tetap berstatus `pending`** meski sudah discrape hari itu.
   Sumber: `app/api/v1/facebook/router.py:412-432` (pemanggil),
   `app/api/v1/facebook/router.py:435-483` (`_scrape_now_and_respond`, lihat
   parameter `mark_topic=None` di baris 431).
3. **"Kalau kuota habis, jadwalkan dan pending" cuma benar untuk budget
   internal kita sendiri**, bukan kuota Apify beneran:
   - Budget internal (5 topik/hari): sisa topik di luar top-5 otomatis nunggu
     besok karena query cuma ambil sejumlah budget. Ini yang bekerja seperti
     dugaan user. Sumber: `trend_scrape_service.py:60-67`.
   - Kuota Apify beneran habis (kredit/limit API Apify sendiri): **TIDAK ADA
     penanganan khusus.** Semua jenis kegagalan (kuota habis, akun tidak
     ketemu, network error, dll) dihitung SAMA di
     `mark_failed_permanent_if_exhausted()` — 3x gagal apapun sebabnya →
     topik ditandai `failed_permanent` selamanya. Kalau kuota Apify habis 3
     hari berturut-turut, topik yang sebenarnya VALID ikut mati permanen,
     bukan cuma "dijadwalkan ulang".
     Sumber: `app/services/trend_recommendations/service.py:111-140`
     (`FAILED_PERMANENT_THRESHOLD = 3`, baris 16).

## 3. Yang KURANG (gap nyata)

### Gap 1 — Tidak ada fallback provider
Facebook cuma pakai Apify (`PROVIDERS = {"apify": ApifyFacebookProvider}`,
`facebook_search_provider_order` default `"apify"` saja). Beda dari
Instagram/TikTok yang punya EnsembleData sebagai cadangan. Kalau Apify
down/kuota habis, Facebook 100% berhenti total, tidak ada provider kedua yang
otomatis dicoba.
Sumber: `app/services/facebook/providers/registry.py:27-30`,
`app/shared/config.py:110`.

**Status:** user akan cari third-party alternatif lain yang valid sendiri
(di luar scope perbaikan kode saat ini).

### Gap 2 — Tidak ada deteksi "kuota habis" vs "gagal permanen"
Error kuota (misal HTTP 402/429 dari Apify) diperlakukan sama dengan "topik
ini genuinely tidak ada / tidak bisa discrape". Harusnya kuota habis
di-skip dari hitungan `failed_permanent`, bukan ikut menghabiskan jatah 3x
kegagalan.

**Status:** user minta rekomendasi — lihat bagian "Rekomendasi" di bawah.

### Gap 3 — Duplikasi scrape di tingkat 3 `/posts/search`
Topik baru dari discover langsung discrape (`_scrape_now_and_respond`) +
tetap berstatus `pending` untuk batch harian besok (karena `mark_topic=None`)
→ berpotensi scrape ulang keyword yang sama, buang kuota Apify.

**Status:** user setuju ikut rekomendasi — akan dieksekusi.

---

## Rekomendasi (Gap 2 — kuota habis vs failed_permanent) — SUDAH DIIMPLEMENTASI

Keputusan akhir (user setuju 2026-07-09): deteksi kuota/rate-limit Apify pakai
2 lapis, tag di `error_message` (bukan kolom/status baru), exclude dari
hitungan `failed_permanent`.

**File baru: `app/shared/apify_errors.py`**
- `is_quota_error(exc, message)` — dua lapis deteksi:
  1. **Structured (akurat):** cek `exc.status_code` (duck typing, tidak import
     `apify_client`) — `apify_client.errors.ApifyApiError` (dan subclass-nya
     seperti `RateLimitError`) SUDAH taruh HTTP status code ASLI dari Apify di
     attribute ini. `402` = payment required (kuota/kredit habis), `429` =
     rate limit. Diverifikasi langsung dari source `apify_client 3.0.3`
     (`site-packages/apify_client/errors.py`) — terinstall via `apify-client
     = "^1.7.0"` di `pyproject.toml:34`.
  2. **Fallback teks:** cocokkan pesan error ke keyword umum (`insufficient`,
     `credit`, `quota`, `usage-hard-limit`, `rate limit`, `monthly usage`,
     `too many requests`) — dipakai kalau exception aslinya sudah hilang
     (cuma tersisa string).
- `tag_if_quota_error(message, exc)` — prefix `"[QUOTA] "` ke pesan kalau
  match, dipakai saat menyusun `ScrapeRun.error_message`.
- Diuji standalone (tanpa dependency lain) via inline script, 5 skenario
  (429 structured, 402 structured, wrapper `ExternalAPIError` status_code=502
  TIDAK match via kode — benar, karena kode asli sudah hilang di titik itu;
  fallback teks; error generik non-kuota) — semua lolos.

**Titik integrasi (paling awal exception Apify asli masih pegang, sebelum
jadi string) — `app/services/facebook/pipeline_service.py:281-283`:**
```python
except Exception as exc:
    errors.append(tag_if_quota_error(f"provider: {exc}", exc=exc))
    rows = []
```
Ini titik SATU-SATUNYA yang dilewati baik jalur scrape-sekarang (tingkat 2/3
`/posts/search`, `_scrape_now_and_respond`) maupun batch harian
(`run_daily_trend_scrape_facebook`), jadi cukup diubah sekali.

**Exclude dari hitungan — `app/services/trend_recommendations/service.py:140-150`
(`mark_failed_permanent_if_exhausted`):**
```python
failed_count = await db.scalar(
    select(func.count()).select_from(ScrapeRun)
    .where(
        ScrapeRun.keyword_text == topic.topic,
        ScrapeRun.status == "failed",
        or_(
            ScrapeRun.error_message.is_(None),
            ~ScrapeRun.error_message.like(f"{QUOTA_ERROR_PREFIX}%"),
        ),
    )
)
```
Perhatian teknis: `ScrapeRun.error_message.is_(None)` HARUS ada di `or_()`,
karena di SQL, `NULL LIKE '...'` hasilnya `NULL` (bukan `False`), dan
`NOT NULL` juga `NULL` — kalau cuma pakai `~error_message.like(...)` tanpa
klausa NULL, baris gagal yang `error_message`-nya `NULL` akan DIAM-DIAM
ke-exclude dari hitungan (bug tersembunyi). Sudah ditangani.

**Cakupan saat ini:** cuma Facebook yang dipasangi tagging (sesuai konteks
diskusi). `mark_failed_permanent_if_exhausted()` dipakai bareng oleh TikTok
juga (`app/services/tiktok/trend_scrape_service.py`) — TikTok OTOMATIS ikut
diuntungkan begitu `tiktok/pipeline_service.py` dipasangi `tag_if_quota_error`
yang sama (BELUM dikerjakan, tidak masuk scope sesi ini, tidak ada regresi
kalau belum dikerjakan — TikTok tetap berperilaku seperti sebelumnya).

**Batasan yang disadari (dicatat untuk tahap cek-ulang sebelum migrasi):**
- Lapis fallback teks (poin 2) bisa diam-diam berhenti bekerja kalau Apify
  ubah format pesan error — lapis structured (poin 1) tidak kena masalah ini.
- `app/integrations/apify/facebook.py:54-55` (kasus actor run selesai dengan
  `status != "SUCCEEDED"`, misal `ABORTED`) TIDAK disentuh — raise
  `ExternalAPIError` generik tanpa detail lebih lanjut dari Apify (field
  `status_message` actor run belum diverifikasi ada/tidak di `apify_client`,
  sengaja tidak ditebak untuk hindari resiko `AttributeError` di production).
  Kalau kuota habis biasanya actor GAGAL START (raise `ApifyApiError`
  402/429 sebelum sempat jalan) — sudah tertangkap lapis 1. Skenario abort
  DI TENGAH run karena kuota habis belum tentu tertangani penuh.
