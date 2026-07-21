GET /facebook/posts/search?q=keyword
        │
        ▼
[1] Cari di posts.content + entities (hashtag) — database lokal
        │
        ├─ KETEMU → return langsung, Apify TIDAK dipanggil (sudah dibuktikan: "Sayang Mama")
        │
        └─ TIDAK ketemu
                │
                ▼
        [2] Cari topik cocok di trend_recommendations (baca saja)
                │
                ├─ TIDAK ketemu topik juga → pesan "tidak ditemukan"
                │
                └─ KETEMU topik + akun Facebook-nya
                        │
                        ▼
                [3] scrape_facebook_posts_via_provider() → panggil Apify
                        │
                        ▼
                [4] Post baru disimpan ke posts, komentar ke comments,
                    hashtag ke entities, sentimen di-dispatch
                        │
                        ▼
                Topik ditandai status='used', return hasil scrape


jadi bagaimana pencarian jika tidak ada di datbase,apakah melakukan post, coba lihat