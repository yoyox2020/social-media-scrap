# Database — Akses & Struktur

Dokumentasi lengkap cara mengakses PostgreSQL dan melihat struktur tabel pada project Social Media Intelligence.

---

## 1. Cara Akses Database

### Masuk ke PostgreSQL Shell (interaktif)

```bash
docker compose exec postgres psql -U social_intelligence -d social_intelligence_db
```

### Jalankan Query Langsung (tanpa masuk shell)

```bash
docker compose exec postgres psql -U social_intelligence -d social_intelligence_db -c "SELECT COUNT(*) FROM users;"
```

---

## 2. Perintah Dasar di dalam psql

```sql
-- Lihat semua tabel
\dt

-- Lihat struktur tabel (kolom + tipe data + index + FK)
\d nama_tabel

-- Lihat detail lengkap (termasuk storage & trigger)
\d+ nama_tabel

-- Lihat semua database
\l

-- Ganti database
\c nama_database

-- Keluar dari psql
\q
```

---

## 3. Ringkasan Semua Tabel

```sql
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
```

| Tabel | Fungsi |
|---|---|
| `users` | Akun pengguna sistem |
| `projects` | Project milik user |
| `keywords` | Kata kunci yang dipantau |
| `posts` | Video YouTube yang di-scrape |
| `comments` | Komentar dari video |
| `lexicon_analyses` | Hasil analisis sentimen tiap komentar |
| `trending_topics` | Topik trending dari Google Trends |
| `sentiments` | Sentimen dari model AI (IndobERT) |
| `entities` | Named entity recognition hasil |
| `topics` | Topik hasil clustering |
| `trends` | Data trend time-series |
| `reports` | Laporan yang di-generate |
| `api_keys` | API key milik user |
| `audit_logs` | Log aktivitas sistem |
| `alembic_version` | Versi migrasi database |

---

## 4. Struktur Tabel Detail

### `users`
```sql
\d users
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key, auto-generate |
| `email` | varchar(255) | Unique, untuk login |
| `username` | varchar(100) | Unique |
| `hashed_password` | varchar(255) | Bcrypt hash |
| `role` | varchar(50) | `user` / `admin` |
| `is_active` | boolean | Status akun |
| `is_superuser` | boolean | Hak superuser |
| `created_at` | timestamptz | Waktu dibuat |
| `updated_at` | timestamptz | Waktu diupdate |

---

### `projects`
```sql
\d projects
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `user_id` | uuid | FK → users.id |
| `name` | varchar(255) | Nama project |
| `description` | text | Deskripsi |
| `is_active` | boolean | Status aktif |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `keywords`
```sql
\d keywords
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `project_id` | uuid | FK → projects.id |
| `keyword` | varchar(255) | Kata kunci pencarian |
| `is_active` | boolean | Status aktif |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `posts` (Video YouTube)
```sql
\d posts
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `keyword_id` | uuid | FK → keywords.id |
| `external_id` | varchar(255) | YouTube video ID |
| `platform` | varchar(50) | `youtube` |
| `content` | text | Judul video |
| `author` | varchar(255) | Nama channel |
| `url` | varchar(2048) | Link YouTube |
| `embedding` | vector(1024) | BGE-M3 embedding |
| `metadata` | jsonb | views, thumbnail, duration dll |
| `raw_data` | jsonb | Raw response API |
| `view_count` | integer | Jumlah views |
| `like_count` | integer | Jumlah likes |
| `comment_count` | integer | Jumlah komentar |
| `collected_at` | timestamptz | Waktu di-scrape |
| `published_at` | timestamptz | Waktu publish video |
| `cleaned_content` | text | Konten setelah preprocessing |
| `language` | varchar(10) | Bahasa terdeteksi |
| `is_processed` | boolean | Sudah diproses NLP? |
| `is_near_duplicate` | boolean | Duplikat terdeteksi? |

---

### `comments`
```sql
\d comments
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `post_id` | uuid | FK → posts.id |
| `external_id` | varchar(255) | YouTube comment ID |
| `content` | text | Isi komentar |
| `author` | varchar(255) | Nama akun |
| `embedding` | vector(1024) | BGE-M3 embedding |
| `metadata` | jsonb | like_count, reply_count, published_time |
| `published_at` | timestamptz | Waktu komentar ditulis |

---

### `lexicon_analyses` (Hasil Sentimen)
```sql
\d lexicon_analyses
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `comment_id` | uuid | FK → comments.id |
| `keyword_id` | uuid | FK → keywords.id |
| `label` | varchar(20) | `positif` / `negatif` / `netral` |
| `score` | float | Skor sentimen (-N s/d +N) |
| `matched_positive` | jsonb | Kata positif yang cocok |
| `matched_negative` | jsonb | Kata negatif yang cocok |
| `removed_stopwords` | jsonb | Stopword yang dihapus |

---

### `trending_topics`
```sql
\d trending_topics
```
| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | uuid | Primary key |
| `rank` | integer | Peringkat trending |
| `title` | text | Judul topik trending |
| `traffic` | varchar | Volume pencarian |
| `description` | text | Deskripsi |
| `geo` | varchar(10) | Region (contoh: `ID`) |
| `period` | varchar | Periode (`24h`, `7d`) |
| `published_at` | timestamptz | Waktu trending |
| `fetched_at` | timestamptz | Waktu di-fetch |

---

## 5. Relasi Antar Tabel

```
users
  └── projects          (user_id → users.id)
        └── keywords    (project_id → projects.id)
              └── posts (keyword_id → keywords.id)
                    └── comments          (post_id → posts.id)
                          └── lexicon_analyses  (comment_id, keyword_id)
```

---

## 6. Query Berguna

### Cek ringkasan semua data
```sql
SELECT
  (SELECT COUNT(*) FROM users)                           AS users,
  (SELECT COUNT(*) FROM projects)                        AS projects,
  (SELECT COUNT(*) FROM keywords)                        AS keywords,
  (SELECT COUNT(*) FROM posts WHERE platform='youtube')  AS videos,
  (SELECT COUNT(*) FROM comments)                        AS comments,
  (SELECT COUNT(*) FROM lexicon_analyses)                AS analyzed,
  (SELECT COUNT(*) FROM trending_topics)                 AS trending;
```

### Cek semua keyword + statistik
```sql
SELECT
  k.keyword,
  COUNT(DISTINCT p.id)  AS videos,
  COUNT(DISTINCT c.id)  AS comments,
  COUNT(DISTINCT la.id) AS analyzed,
  k.created_at::date    AS sejak
FROM keywords k
LEFT JOIN posts p  ON p.keyword_id = k.id AND p.platform = 'youtube'
LEFT JOIN comments c ON c.post_id = p.id
LEFT JOIN lexicon_analyses la ON la.keyword_id = k.id
GROUP BY k.id, k.keyword, k.created_at
ORDER BY videos DESC;
```

### Cari keyword tertentu
```sql
SELECT id, keyword, created_at FROM keywords WHERE keyword ILIKE '%demo%';
```

### Lihat video terbaru
```sql
SELECT p.content AS judul, p.author AS channel, k.keyword, p.collected_at
FROM posts p
JOIN keywords k ON k.id = p.keyword_id
WHERE p.platform = 'youtube'
ORDER BY p.collected_at DESC
LIMIT 10;
```

### Lihat komentar + sentimen
```sql
SELECT
  c.content    AS komentar,
  c.author,
  la.label     AS sentimen,
  la.score,
  k.keyword
FROM comments c
JOIN posts p ON p.id = c.post_id
JOIN keywords k ON k.id = p.keyword_id
JOIN lexicon_analyses la ON la.comment_id = c.id
ORDER BY c.created_at DESC
LIMIT 20;
```

### Distribusi sentimen per keyword
```sql
SELECT
  k.keyword,
  la.label,
  COUNT(*) AS jumlah,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY k.id), 1) AS persen
FROM lexicon_analyses la
JOIN keywords k ON k.id = la.keyword_id
GROUP BY k.id, k.keyword, la.label
ORDER BY k.keyword, la.label;
```

### Cek versi migrasi
```sql
SELECT * FROM alembic_version;
```

---

## 7. Akses di Server Ubuntu

```bash
# Masuk ke shell PostgreSQL
docker compose exec postgres psql -U social_intelligence -d social_intelligence_db

# Query langsung
docker compose exec postgres psql -U social_intelligence -d social_intelligence_db \
  -c "SELECT COUNT(*) FROM posts WHERE platform='youtube';"

# Export hasil query ke CSV
docker compose exec postgres psql -U social_intelligence -d social_intelligence_db \
  -c "\COPY (SELECT * FROM keywords) TO '/tmp/keywords.csv' CSV HEADER;"
```

---

## 8. Koneksi dari Luar Container (opsional)

Jika PostgreSQL di-expose ke host (`5432:5432` di docker-compose.yml):

```bash
# Dari host machine
psql -h localhost -p 5432 -U social_intelligence -d social_intelligence_db

# Connection string
postgresql://social_intelligence:password@localhost:5432/social_intelligence_db
```

> **Catatan:** Untuk production, jangan expose port 5432 ke publik. Gunakan hanya internal Docker network.
