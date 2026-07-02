-- Tabel untuk menyimpan akun Instagram trending hasil discovery harian
-- Jalankan sekali di server: psql $DATABASE_URL -f scripts/create_instagram_trending_table.sql

CREATE TABLE IF NOT EXISTS instagram_trending_accounts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username         VARCHAR(100)  NOT NULL,
    display_name     VARCHAR(255)  NOT NULL DEFAULT '',
    source           VARCHAR(50)   NOT NULL DEFAULT 'ensembledata',
    discovered_via   VARCHAR(255),
    rank             INTEGER,
    trending_score   FLOAT         NOT NULL DEFAULT 0,
    engagement_rate  FLOAT         NOT NULL DEFAULT 0,
    virality_score   FLOAT         NOT NULL DEFAULT 0,
    followers        INTEGER       NOT NULL DEFAULT 0,
    posts_collected  INTEGER       NOT NULL DEFAULT 0,
    status           VARCHAR(20)   NOT NULL DEFAULT 'active',
    last_scraped_date DATE,
    scrape_logs      JSONB         NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_instagram_trending_username ON instagram_trending_accounts(username);
CREATE INDEX IF NOT EXISTS idx_instagram_trending_status  ON instagram_trending_accounts(status);
CREATE INDEX IF NOT EXISTS idx_instagram_trending_source  ON instagram_trending_accounts(source);
CREATE INDEX IF NOT EXISTS idx_instagram_trending_rank    ON instagram_trending_accounts(rank);
