-- Seed data test untuk instagram_trending_accounts
-- Jalankan di server: docker exec social_intel_db psql -U social_intelligence -d social_intelligence_db -f /tmp/seed.sql

-- Bersihkan data lama (opsional, untuk fresh test)
-- DELETE FROM instagram_trending_accounts;

-- Insert 5 akun test
INSERT INTO instagram_trending_accounts
    (username, display_name, source, discovered_via, rank, trending_score, engagement_rate, virality_score, followers, status)
VALUES
    ('tukang_jelajah',  'Tukang Jelajah',   'ensembledata', '#indonesia', 1, 14.15, 25.53, 2.77, 15000,   'active'),
    ('radityadika',     'Raditya Dika',      'ensembledata', '#viral',     2, 11.69, 19.92, 3.46, 120000,  'active'),
    ('awkarin',         'Karin Novilda',     'ensembledata', '#fyp',       3, 8.40,  15.20, 1.60, 450000,  'active'),
    ('awkarin2test',    'Test Akun 4',       'ensembledata', '#trending',  4, 3.20,  6.10,  0.30, 80000,   'active'),
    ('cndigital_test',  'Celebes Nusa Test', 'ensembledata', '#indonesia', 5, 0.14,  0.24,  0.04, 2000000, 'active')
ON CONFLICT DO NOTHING;

SELECT id, username, rank, trending_score, status FROM instagram_trending_accounts ORDER BY rank;
