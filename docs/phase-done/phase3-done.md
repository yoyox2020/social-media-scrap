# Phase 3 — Processing Service ✅

**Status:** SELESAI  
**Tanggal:** 2026-06-22  
**Branch:** main

---

## Ringkasan

Phase 3 mengimplementasikan pipeline pembersihan dan normalisasi teks sebelum data masuk ke AI model (Phase 5). Semua post yang sudah dikumpulkan di Phase 2 diproses untuk menghasilkan `cleaned_content` yang bersih, label bahasa, dan deteksi near-duplicate.

---

## Komponen Baru

### 1. TextCleaner (`app/services/processing/cleaner.py`)

Membersihkan teks raw dari social media:

| Langkah | Deskripsi |
|---------|-----------|
| HTML entity decode | `&amp;` → `&`, `&lt;` → `<` |
| Strip HTML tags | `<b>teks</b>` → `teks` |
| Hapus URL | `https://...` dihapus |
| Hapus mention | `@username` dihapus |
| Expand hashtag | `#python` → `python` |
| Hapus emoji | Range Unicode emoji dihapus |
| Normalisasi whitespace | Multiple spasi → 1 spasi |

```python
from app.services.processing.cleaner import TextCleaner

cleaner = TextCleaner()
clean = cleaner.clean("<b>Produk ini</b> 😊 bagus! https://toko.com #rekomendasi")
# → "Produk ini bagus rekomendasi"
```

### 2. TextNormalizer (`app/services/processing/text_normalizer.py`)

Normalisasi teks untuk input NLP/AI model:

- **Lowercase** — semua huruf kecil
- **Ekspansi slang Indonesia** — 50+ singkatan umum (`yg` → `yang`, `gak` → `tidak`, `bgt` → `banget`, dll.)
- **Stopword removal** — opsional, default off (agar model AI punya konteks penuh)
- **Tokenisasi** — `.tokenize()` return list kata
- **Language detection** — heuristik berbasis indicator words Indonesia vs English

```python
from app.services.processing.text_normalizer import TextNormalizer

norm = TextNormalizer()
norm.normalize("gw gak mau pergi dgn dia")
# → "saya tidak mau pergi dengan dia"

norm.detect_language("saya tidak tahu kenapa dia tidak mau datang")
# → "id"
```

**Bahasa yang dapat dideteksi:**
- `id` — Bahasa Indonesia
- `en` — English
- `unknown` — tidak dapat ditentukan

### 3. NearDuplicateDetector (`app/services/processing/deduplicator.py`)

Mendeteksi konten yang hampir sama menggunakan **character shingling + Jaccard similarity**:

- **Algoritma:** Character k-gram (default k=4) + Jaccard similarity
- **Threshold:** default 0.85 (85% mirip → near-duplicate)
- **Window:** 200 post sebelumnya (menghindari O(n²))
- **Skip:** Teks < 20 karakter dilewati

```python
from app.services.processing.deduplicator import NearDuplicateDetector

detector = NearDuplicateDetector(threshold=0.85)
dups = detector.find_duplicates(post_ids, cleaned_contents)
# → [DuplicateResult(post_id=..., duplicate_of=..., similarity=0.92)]
```

> **Catatan:** Phase 5 akan mengganti ini dengan embedding similarity menggunakan pgvector + BGE-M3 untuk mendeteksi parafrase semantik.

### 4. ProcessingService (`app/services/processing/service.py`)

Orkestrasi pipeline end-to-end per keyword:

```
posts DB → clean → detect language → near-dedup → bulk update DB
```

```python
from app.services.processing.service import ProcessingService

service = ProcessingService(db)
result = await service.process_keyword(keyword_id=..., force_reprocess=False)
# ProcessResult(total_posts=50, cleaned=50, near_duplicates_found=3, errors=[])
```

---

## Perubahan Database

### Migration 003 (`migrations/versions/003_posts_processing_columns.py`)

Kolom baru di tabel `posts`:

| Kolom | Tipe | Default | Keterangan |
|-------|------|---------|------------|
| `cleaned_content` | TEXT | NULL | Teks bersih hasil pipeline |
| `language` | VARCHAR(10) | NULL | `id`, `en`, atau `unknown` |
| `is_processed` | BOOLEAN | false | Apakah post sudah diproses |
| `is_near_duplicate` | BOOLEAN | false | Apakah near-duplicate terdeteksi |

Index baru: `ix_posts_is_processed`, `ix_posts_language`

---

## API Endpoints Baru

Base prefix: `/api/v1/processing`

| Method | Path | Deskripsi |
|--------|------|-----------|
| `POST` | `/trigger` | Trigger processing via Celery (async, 202) |
| `POST` | `/trigger-sync` | Jalankan processing sinkron (untuk dev/debug) |
| `GET` | `/stats/{keyword_id}` | Statistik processing satu keyword |

### Contoh Request: Trigger Processing

```http
POST /api/v1/processing/trigger
Authorization: Bearer {token}
Content-Type: application/json

{
  "keyword_id": "550e8400-e29b-41d4-a716-446655440000",
  "force_reprocess": false
}
```

```json
{
  "success": true,
  "data": {
    "keyword_id": "550e8400-e29b-41d4-a716-446655440000",
    "job_id": "3e4d5f6g-...",
    "status": "queued"
  }
}
```

### Contoh Response: Stats

```json
{
  "success": true,
  "data": {
    "keyword_id": "550e8400-...",
    "total_posts": 150,
    "processed": 147,
    "near_duplicates": 8,
    "language_breakdown": { "id": 120, "en": 27 }
  }
}
```

---

## Celery Task Baru

**Task name:** `workers.process_posts`

```python
from app.workers.processing_worker import process_posts_task

# Async via Celery
task = process_posts_task.delay(keyword_id="...", force_reprocess=False)

# Cek status (sama dengan collector jobs)
# GET /api/v1/collectors/jobs/{task.id}
```

---

## Alur Lengkap (Phase 2 + 3)

```
User POST /collect
    ↓
Celery: collect_posts_task
    ↓
Data raw tersimpan di posts.raw_data + posts.content
    ↓
User POST /processing/trigger
    ↓
Celery: process_posts_task
    ↓
TextCleaner.clean(content) → cleaned_content
TextNormalizer.detect_language() → language
NearDuplicateDetector.find_duplicates() → is_near_duplicate
    ↓
UPDATE posts SET cleaned_content=..., language=..., is_processed=true
    ↓
Data siap untuk Phase 5 (AI: IndoBERT sentiment, GLiNER NER, BGE-M3 embedding)
```

---

## Tests

**32 unit tests, semua PASSED:**

| File | Tests | Coverage |
|------|-------|----------|
| `test_cleaner.py` | 10 | HTML, URL, mention, hashtag, emoji, whitespace, batch |
| `test_text_normalizer.py` | 12 | Lowercase, slang, stopwords, tokenize, lang detection |
| `test_deduplicator.py` | 10 | Jaccard, near-dup, window, edge cases |

```
======================== 32 passed in 0.24s ========================
```

---

## File yang Dibuat/Dimodifikasi

```
app/
├── domain/posts/models.py                    ← MODIFIED (4 kolom baru)
├── services/processing/
│   ├── cleaner.py                            ← NEW
│   ├── text_normalizer.py                    ← NEW
│   ├── deduplicator.py                       ← NEW
│   ├── service.py                            ← NEW
│   └── schemas.py                            ← NEW
├── workers/
│   ├── processing_worker.py                  ← NEW
│   └── celery_app.py                         ← MODIFIED (include processing)
├── api/v1/
│   └── processing.py                         ← NEW
└── main.py                                   ← MODIFIED (processing router)
migrations/versions/
└── 003_posts_processing_columns.py           ← NEW
tests/unit/
├── test_cleaner.py                           ← NEW
├── test_text_normalizer.py                   ← NEW
└── test_deduplicator.py                      ← NEW
docs/phase-done/
└── phase3-done.md                            ← NEW (dokumen ini)
```

---

## Cara Menjalankan

```bash
# Jalankan migration baru
make migrate

# Atau langsung dengan docker
docker compose exec api alembic upgrade head

# Test processing via sync endpoint (tanpa Celery)
curl -X POST http://localhost:8000/api/v1/processing/trigger-sync \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"keyword_id": "...", "force_reprocess": false}'

# Cek statistik
curl http://localhost:8000/api/v1/processing/stats/{keyword_id} \
  -H "Authorization: Bearer {token}"
```

---

## Siap untuk Phase 4 — AI Service

Data yang sudah melalui processing pipeline (Phase 3) siap digunakan oleh:
- **IndoBERT** — analisis sentimen dari `cleaned_content`
- **GLiNER** — named entity recognition dari `cleaned_content`
- **BGE-M3** — generate embedding 1024 dimensi → simpan ke `posts.embedding` (pgvector)
- **Qwen3 8B** — reasoning dan summarization
