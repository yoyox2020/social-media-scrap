# Test Access Instagram via Apify — Metode & Hasil

Dokumen ini mencatat hasil eksperimen test akses Instagram (dan Facebook/TikTok)
memakai Apify Actor, sebagai alternatif dari EnsembleData (metode utama project
ini, lihat [instagram scrapping method.md](instagram%20scrapping%20method.md))
saat subscription EnsembleData expired (error 493).

Script kerja:
- [scripts/apify_instagram_test.py](../scripts/apify_instagram_test.py) — Instagram
- [scripts/apify_facebook_test.py](../scripts/apify_facebook_test.py) — Facebook

---

## Actor yang dipakai

- **Actor ID:** `ycQuEFDDZmgX7BAsL` (nama: `social-media-sentiment-analysis-tool`)
- Wrapper actor yang di dalamnya memanggil beberapa sub-actor Apify:
  - Facebook: `apify/facebook-page-contact-information`, `apify/facebook-posts-scraper`
  - Instagram: `apify/instagram-scraper`, `apify/instagram-comment-scraper`
  - TikTok: scraper TikTok bawaan + `clockworks/tiktok-comments-scraper`
- Fitur bawaan: sentiment analysis otomatis per komentar (`positive` / `neutral` / `negative` + score).

## Cara pakai

```bash
pip install apify_client
export APIFY_API_TOKEN="apify_api_..."   # jangan commit token ke repo
python scripts/apify_instagram_test.py <username> [latest_posts] [latest_comments]

# contoh
python scripts/apify_instagram_test.py starbucks 5 3
```

Output tersimpan ke `apify_instagram_<username>.json` — satu baris per pasangan
(post, comment), sudah termasuk skor sentimen.

## Input schema (field yang valid)

```python
run_input = {
    "instagramProfileName": "starbucks",   # atau facebookProfileName / tiktokProfileName
    "scrapeFacebook": False,
    "scrapeInstagram": True,
    "scrapeTiktok": False,
    "sentimentAnalysis": True,
    "latestPosts": 5,
    "latestComments": 3,
}
```

## Gotcha yang ditemukan saat testing

1. **`latestComments` wajib > 0.**
   Actor menyusun output per pasangan (post, comment) — kalau `latestComments=0`,
   post tetap berhasil di-fetch (terlihat di log `[Instagram] Fetched posts...`)
   tapi dataset akhir **kosong (0 item)**. Tidak ada mode "posts only" tanpa komentar.

2. **Field opsional tidak boleh `null`.**
   `dateFrom`, `dateTo`, dan field profile platform yang tidak dipakai
   (`facebookProfileName`, dll saat `scrapeFacebook=False`) harus **dihilangkan
   dari dict**, bukan diisi `None` — Actor menolak dengan
   `Input is not valid: Field input.xxx must be string`.

3. **Bug internal Actor untuk Instagram comment-scraper pada akun tertentu.**
   Saat test pertama pakai akun kecil (`redteam.id`) dengan scrape gabungan
   Facebook+Instagram+TikTok, langkah komentar Instagram gagal:
   ```
   ERROR There was an issue with scraping Instagram: Input is not valid:
   Values in input.directUrls at positions [0] must match regular expression
   "https?:\/\/(?:www\.)?instagram\.com\/...\/(?:p|reel)\/[^\/]+"
   ```
   Root cause-nya adalah gotcha #1 di atas (waktu itu belum ketahuan) — begitu
   `latestComments` diisi angka > 0 dan akun yang dites punya post publik yang
   cukup aktif (`starbucks`), proses berjalan normal tanpa error ini.
   **Belum dikonfirmasi 100%** apakah akun kecil/sepi post seperti `redteam.id`
   tetap bisa gagal karena alasan lain (post terlalu sedikit/private) — perlu
   di-retest kalau dibutuhkan.

4. **Facebook: hasil 0 post sangat tergantung page-nya.**
   Page brand global besar (`facebook.com/starbucks`) mengembalikan
   `"[Facebook] There have been 0 posts found..."` walau run tetap `SUCCEEDED`
   — kemungkinan page tsb memblokir/redirect scraper tanpa login. Page media
   Indonesia (`facebook.com/detikcom`) berhasil normal (3 post, 9 komentar).
   **Kesimpulan:** kalau hasil Facebook 0 post, coba page lain dulu sebelum
   menyimpulkan token/metode-nya rusak.

5. **`facebookProfileName` harus slug URL, bukan nama tampilan.**
   Actor membangun URL langsung dari input (`facebook.com/<facebookProfileName>/`),
   tanpa search engine (`"Not using search engine to look for profiles..."`).
   Input dengan spasi (`"pratiwi noviyanthi"`) menghasilkan URL tidak valid →
   0 post. Bahkan slug yang terlihat benar bisa tetap 0 post kalau itu bukan
   page/profile yang benar-benar aktif — contoh nyata: `PratiwiNoviyanthi`
   (0 post) vs `pratiwinoviyanthireal` (berhasil, 5 post + 15 komentar).
   **Cara cari slug yang benar:** cari nama orang/brand + "facebook.com" via
   web search, ambil slug dari URL hasil pencarian yang paling relevan/aktif,
   baru dicoba di script. Kalau hasil 0 post, coba slug alternatif lain dari
   hasil pencarian yang sama sebelum menyerah.

## Hasil test yang berhasil

| Platform | Username | Post diambil | Komentar diambil | Status |
|---|---|---|---|---|
| Instagram | `starbucks` | 5 post unik | 15 (3/post) | ✅ Berhasil, sentiment lengkap |
| Instagram | `redteam.id` (gabungan FB+IG+TikTok) | — | 0 item (lihat gotcha #1) | ⚠️ Perlu diulang dengan `latestComments>0` |
| TikTok | `redteam.id` | — | 41 item | ✅ Berhasil, sentiment lengkap |
| Facebook | `starbucks` | 0 post | — | ❌ Page kemungkinan diproteksi (lihat gotcha #4) |
| Facebook | `detikcom` | 3 post unik | 9 (3/post) | ✅ Berhasil, sentiment lengkap |
| Facebook | `PratiwiNoviyanthi` | 0 post | — | ❌ Slug salah/tidak aktif (lihat gotcha #5) |
| Facebook | `pratiwinoviyanthireal` | 5 post unik | 15 (3/post) | ✅ Berhasil, sentiment lengkap |

Contoh 1 baris hasil (`starbucks`):

```json
{
  "targetPlatform": "instagram",
  "profileName": "starbucks",
  "postUrl": "https://www.instagram.com/p/DZ6Heo5jdbf/",
  "postDescription": "nothing to see here",
  "postLikesCount": 87333,
  "postCommentsCount": 1546,
  "commentText": "Literally nothing 🙄",
  "commentAuthor": "itschar.oluwaseyi",
  "sentiment": {
    "finalClassification": "negative",
    "finalScore": 0.74
  }
}
```

Contoh 1 baris hasil (`pratiwinoviyanthireal`, Facebook):

```json
{
  "targetPlatform": "facebook",
  "profileName": "Pratiwi Noviyanthi",
  "profileUrl": "https://www.facebook.com/pratiwinoviyanthireal/",
  "profileFollowers": 8304381,
  "postUrl": "https://www.facebook.com/reel/1346922540730952/",
  "postDescription": "Terimakasih Telah Hadir di Hidup Ini Karena Pertemanan Terbaik",
  "postLikesCount": 294,
  "postCommentsCount": 104,
  "commentText": "MasyaAllah",
  "commentAuthor": "Mega Ristin Yulika",
  "sentiment": {
    "finalClassification": "positive",
    "finalScore": 0.499
  }
}
```

## Perbandingan singkat dengan metode lain di project ini

| Metode | Status akses | Catatan |
|---|---|---|
| **EnsembleData** (metode utama, lihat [instagram scrapping method.md](instagram%20scrapping%20method.md)) | Error 493 — subscription expired | Recovery otomatis begitu subscription renewal, tidak perlu ubah kode |
| **Meta Graph API resmi** (token Facebook User/Page) | Sebagian jalan — `/posts` field dasar OK, tapi `likes`/`comments`/`reactions` butuh Advanced Access; `pages_read_user_content` belum digrant | Hanya bisa akses Page yang dikelola sendiri, tidak bisa cari page publik sembarangan (endpoint search sudah dideprecate Meta) |
| **Apify** (dokumen ini) | ✅ Berhasil untuk akun publik manapun (tidak perlu Page terhubung) | Berbayar per run (compute units Apify), ada gotcha input schema di atas |

## Keamanan

Token Apify **jangan dihardcode** di script atau commit ke repo — selalu lewat
env var `APIFY_API_TOKEN`. Kalau token pernah ter-paste di tempat yang tidak
aman (chat, log, dsb), segera rotate dari [Apify Console](https://console.apify.com/account/integrations).
