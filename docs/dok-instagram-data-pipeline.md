# Dokumentasi: Penyimpanan Data Akun/Post/Komentar/Hashtag/Sentimen Instagram

Dokumentasi ini menjawab pertanyaan: setelah topik dari `trend_recommendations`
dapat akun Instagram-nya (Subsistem B, lihat
`docs/dok-ai-viral-discovery-otomatis.md`), **di tabel mana** hasil scrape
(post, komentar, hashtag, sentimen, foto) benar-benar tersimpan — dan
perbaikan yang dibuat 05 Juli 2026 supaya Instagram konsisten dengan platform
lain (YouTube dkk).

## Peta tabel — Instagram vs platform lain

| Data | Tabel | Platform-agnostic? |
|---|---|---|
| Post | `posts` (`platform='instagram'`) | ✅ Tabel sama dengan YouTube dkk, dibedakan lewat kolom `platform` |
| Komentar | `comments` (FK `posts.id`) | ✅ Tabel sama, generic |
| Hashtag | `entities` (`entity_type='HASHTAG'`) | ✅ **Baru diintegrasikan 05 Juli 2026** — sebelumnya cuma JSON ad-hoc di `posts.metadata_["hashtags"]`, sekarang jadi baris `entities` yang bisa di-query lintas post/platform lewat repository yang sudah ada |
| Sentimen POST | `sentiments` (model IndoBERT, via `AIService.analyze_post()`) | ✅ **Baru diaktifkan untuk Instagram 05 Juli 2026** — sebelumnya cuma jalan untuk platform lain |
| Sentimen KOMENTAR | `lexicon_analyses` (skor lexicon sederhana) | ⚠️ Khusus Instagram, tidak berubah (fitur lama, tetap dipakai) |
| Foto | `posts.metadata_["photo_url"]` | ⚠️ Apify **tidak menyediakan field ini sama sekali** (diverifikasi live — lihat di bawah). EnsembleData mungkin bisa (`image_versions2.candidates[0].url`, format umum private-API Instagram), tapi belum terverifikasi live karena subscription masih expired |
| Akun (followers, bio) | Tidak dipersist — cuma transient di response | Sengaja tidak dibuat tabel baru (lihat bagian dedup di bawah) |

## Perubahan yang dibuat (05 Juli 2026)

Semua di `app/services/instagram/pipeline_service.py` kecuali disebutkan lain:

### 1. Post per akun — sudah bisa diatur, tinggal naikkan default
`instagram_trend_posts_per_topic` (config/`.env`) dinaikkan dari `1` → `3`.
Tidak perlu kode baru — nilai ini sudah dipakai `run_daily_trend_scrape()`
(frozen) sebagai `max_posts` per topik/akun.

### 2. Hashtag → tabel `entities`
Sebelumnya: `_extract_hashtags(caption)` cuma disimpan di JSON
`metadata_["hashtags"]`, tidak bisa di-query terpisah.
Sekarang: tiap hashtag jadi baris `Entity(post_id=..., text=tag,
entity_type="HASHTAG")` — pola yang sama dengan NER (GLiNER), tapi
`entity_type` beda supaya tidak campur dengan hasil NER asli (`PERSON`,
`ORG`, dll).

### 3. Sentimen POST via IndoBERT (bukan lagi cuma komentar)
Sebelumnya: Instagram cuma analisis sentimen KOMENTAR (lexicon sederhana),
POST-nya sendiri tidak pernah dianalisis sama sekali.
Sekarang: tiap post baru men-dispatch task Celery `workers.analyze_post`
(`app/workers/ai_worker.py`, sudah ada — dipakai juga oleh YouTube/viral
tracking) dengan `run_sentiment=True, run_ner=False, run_embedding=False`.
**Penting:** dipanggil lewat `.delay()` (task Celery), BUKAN dipanggil
langsung (`await AIService(db).analyze_post(...)`) — karena IndoBERT butuh
`torch`/`transformers` yang cuma ter-install di container `worker-ai`,
sedangkan `pipeline_service.py` jalan di container `worker`/`worker-beat`/`api`
yang tidak punya ML deps itu.

### 4. Foto — field ditangkap kalau ada, tapi Apify tidak menyediakannya
Ditambahkan `photoUrl` ke bentuk baris kanonik provider (opsional). **Dicek
langsung lewat 1 kali panggilan nyata ke Apify Actor** (`docker exec ...
scrape_instagram_via_apify('starbucks', 1, 1)`, lihat seluruh key hasil):

```
['commentAuthor', 'commentLikesCount', 'commentText', 'commentTimestamp',
 'parentCommentId', 'postCommentsCount', 'postDescription', 'postLikesCount',
 'postTimestamp', 'postUrl', 'profileDescription', 'profileFollowers',
 'profileFollows', 'profileName', 'profileUrl', 'repliesCount', 'targetPlatform']
```

**Tidak ada field gambar sama sekali** — jadi `photo_url` akan selalu `null`
untuk data dari Apify. Untuk EnsembleData, ditambahkan percobaan ekstraksi
`post.get("image_versions2", {}).get("candidates", [{}])[0].get("url")` —
ini format umum respons private-API Instagram, **tapi belum bisa
diverifikasi live** karena subscription EnsembleData masih expired.
Field ini perlu dicek ulang begitu subscription aktif lagi.

### 5. Flag "akun sudah diambil hari ini" — tanpa tabel baru
Sebelum panggil provider, `scrape_instagram_posts()` sekarang cek dulu:
`SELECT COUNT(*) FROM posts WHERE platform='instagram' AND author=<username>
AND date(collected_at) = CURRENT_DATE`. Kalau > 0 → langsung return
`provider_used: "cached_today"`, TIDAK memanggil third-party sama sekali.

**Kenapa ini penting:** beberapa topik `trend_recommendations` berbeda bisa
mengarah ke akun Instagram yang sama (contoh nyata di data live: beberapa
topik "Coldplay" semua mengarah ke `@coldplay`). Tanpa flag ini,
`run_daily_trend_scrape()` akan memanggil third-party berkali-kali untuk
akun yang sama dalam satu run — boros kuota persis seperti kasus duplikat
yang dilaporkan di `dok-ai-viral-discovery-otomatis.md`. Tidak perlu tabel
counter baru — dihitung ulang dari `posts` yang sudah ada, konsisten dengan
pola kuota (`quota_service.py`) yang sudah dipakai sesi ini.

Sudah diverifikasi live: panggil `scrape_instagram_posts(db, 'starbucks', ...)`
dua kali berturut-turut → panggilan kedua langsung `already_scraped_today:
true`, tanpa memanggil Apify sama sekali.

## Bug yang ditemukan & diperbaiki selagi verifikasi

### 1. Sentimen IndoBERT rusak total di SEMUA platform (BUKAN cuma Instagram) — DIPERBAIKI

Saat testing, ditemukan **0 dari 1200 post (1180 YouTube + 20 Instagram)
pernah punya baris `sentiments`** — bug pre-existing, bukan disebabkan
perubahan sesi ini. Root cause: `SentimentAnalyzer.__init__()`
(`app/services/ai/sentiment_analyzer.py`) meneruskan `cache_dir=` langsung
ke `transformers.pipeline(...)`. Dengan `transformers==4.45.2` (versi yang
ter-install), kwarg itu ikut disisipkan ke pemanggilan tokenizer saat
inference (bukan cuma saat download), menyebabkan crash:
`PreTrainedTokenizerFast._batch_encode_plus() got an unexpected keyword
argument 'cache_dir'`.

**Diperbaiki** (dikonfirmasi user): load `AutoTokenizer`/
`AutoModelForSequenceClassification` eksplisit dengan `cache_dir` di situ
saja, baru bungkus jadi `pipeline(model=..., tokenizer=..., ...)` TANPA
`cache_dir`. Diverifikasi live setelah fix: post baru berhasil dapat baris
`sentiments` (`label='neutral', score=0.8146,
model_version='mdhugol/indonesia-bert-sentiment-classification'`).

Perbaikan ini otomatis memperbaiki sentimen POST untuk **semua platform**
(YouTube dkk juga), bukan cuma Instagram.

### 2. Race condition routing Celery `workers.analyze_post` — DITEMUKAN, BELUM DIPERBAIKI (keputusan user)

`celery_app.py` tidak punya `task_routes` untuk `workers.analyze_post`/
`workers.analyze_keyword`. Task ini didispatch lewat `.delay()` tanpa queue
eksplisit → masuk ke queue default `celery`. **Dua container sama-sama
konsumsi queue itu**: `worker` (`--queues=collector,processing,reports,celery`,
TIDAK ada ML deps) dan `worker-ai` (`--queues=ai,celery`, ADA ML deps).
Siapapun yang lebih dulu idle akan mengambil task itu — kalau jatuh ke
`worker`, sentimen langsung gagal (`No module named 'transformers'`), diam-diam
(task tetap "succeeded" dari sisi Celery karena error-nya ketangkep di dalam
`AIService.analyze_post()`, cuma masuk ke `result.errors`, tidak raise).

**Terverifikasi nyata**: dari 2 kali test (`nike`, `natgeo`, masing-masing
2 post baru), **masing-masing test 1 dari 2 task nyasar ke `worker` dan
gagal**, cuma yang jatuh ke `worker-ai` yang berhasil dapat sentimen. Ini
bukan cuma masalah Instagram — task yang sama juga dipakai
`viral_tracking/service.py` dan endpoint `POST /sentiment/analyze-keyword`.

**Perbaikan yang belum dijalankan** (butuh konfirmasi user, ditunda atas
pilihan eksplisit "Belum, dokumentasikan saja dulu"): tambah
```python
task_routes={
    "workers.analyze_post": {"queue": "ai"},
    "workers.analyze_keyword": {"queue": "ai"},
}
```
di `celery_app.conf.update(...)` (`app/workers/celery_app.py`) supaya kedua
task itu SELALU ke `worker-ai`, tidak pernah ke `worker` biasa.

**Dampak selama belum diperbaiki**: sentimen post Instagram (dan platform
lain yang pakai task ini) akan berhasil ~50% kemungkinan tergantung
container mana yang lebih dulu idle saat task di-dispatch — bukan
kegagalan permanen, cuma tidak reliable.

## Ringkasan file yang diubah (05 Juli 2026)

| File | Perubahan |
|---|---|
| `app/shared/config.py` | `instagram_trend_posts_per_topic` default `1` → `3` |
| `app/services/instagram/pipeline_service.py` | dedup akun-per-hari, hashtag→`entities`, dispatch sentimen via Celery task, capture `photoUrl` |
| `app/services/instagram/providers/ensemble_data_provider.py` | tambah percobaan ekstraksi `photoUrl` (belum terverifikasi live) |
| `app/services/ai/sentiment_analyzer.py` | fix bug `cache_dir` yang membuat sentimen rusak total di semua platform |
| `.env` | `INSTAGRAM_TREND_POSTS_PER_TOPIC=3` |

Semua sudah dideploy & diverifikasi live di server production
(187.77.125.10) sebelum di-commit, kecuali fix `task_routes` (item 2 di atas)
yang sengaja ditunda.
