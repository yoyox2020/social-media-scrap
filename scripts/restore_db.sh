#!/bin/bash
# Jalankan di server: bash /tmp/restore_db.sh
# Pastikan file backup sudah ada di /tmp/social_intel_backup_*.sql

BACKUP_FILE=$(ls /tmp/social_intel_backup_*.sql | tail -1)

if [ -z "$BACKUP_FILE" ]; then
  echo "ERROR: File backup tidak ditemukan di /tmp/"
  exit 1
fi

echo "=== Restore dari: $BACKUP_FILE ==="

# Nama container postgres di server (sesuaikan jika berbeda)
DB_CONTAINER=$(docker ps --format "{{.Names}}" | grep -i postgres | head -1)

if [ -z "$DB_CONTAINER" ]; then
  echo "ERROR: Container postgres tidak ditemukan. Jalankan 'docker compose up -d' dulu."
  exit 1
fi

echo "Container DB: $DB_CONTAINER"

# Copy file backup ke dalam container
docker cp "$BACKUP_FILE" "$DB_CONTAINER":/tmp/backup.sql

# Drop semua tabel lama dan restore dari backup
docker exec "$DB_CONTAINER" psql -U social_intelligence -d social_intelligence_db -c "
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO social_intelligence;
"

docker exec "$DB_CONTAINER" psql -U social_intelligence -d social_intelligence_db -f /tmp/backup.sql

echo ""
echo "=== Verifikasi data ==="
docker exec "$DB_CONTAINER" psql -U social_intelligence -d social_intelligence_db -c "
SELECT
  (SELECT COUNT(*) FROM keywords) AS keywords,
  (SELECT COUNT(*) FROM posts WHERE platform='youtube') AS videos,
  (SELECT COUNT(*) FROM comments) AS comments,
  (SELECT COUNT(*) FROM lexicon_analyses) AS sentimen,
  (SELECT COUNT(*) FROM trending_topics) AS trending;
"

echo ""
echo "=== Selesai. Restart API container ==="
docker restart social_intel_api 2>/dev/null || docker compose restart api
