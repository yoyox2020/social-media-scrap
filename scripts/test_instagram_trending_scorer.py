"""
Test scorer Instagram trending — jalankan lokal tanpa server/DB.

python scripts/test_instagram_trending_scorer.py
"""
import sys
sys.path.insert(0, ".")

from app.services.instagram_trending.scorer import calculate

print("=== Test Trending Scorer ===\n")

# Simulasi akun dengan engagement tinggi
cases = [
    {
        "label": "Akun viral (views besar, follower sedang)",
        "followers": 120_000,
        "posts": [
            {"likes": 25_000, "comments": 1_500, "views": 520_000},
            {"likes": 18_000, "comments": 900,   "views": 310_000},
        ],
    },
    {
        "label": "Akun micro-influencer (engagement tinggi, follower kecil)",
        "followers": 15_000,
        "posts": [
            {"likes": 3_200, "comments": 450, "views": 45_000},
            {"likes": 2_800, "comments": 380, "views": 38_000},
        ],
    },
    {
        "label": "Akun besar tapi engagement rendah",
        "followers": 2_000_000,
        "posts": [
            {"likes": 5_000, "comments": 100, "views": 80_000},
            {"likes": 4_200, "comments": 80,  "views": 65_000},
        ],
    },
    {
        "label": "Akun baru tanpa data",
        "followers": 500,
        "posts": [],
    },
]

ranked = []
for c in cases:
    score = calculate(c["posts"], c["followers"])
    ranked.append((c["label"], score))
    print(f"[{c['label']}]")
    print(f"  engagement_rate : {score.engagement_rate:.4f}%")
    print(f"  virality_score  : {score.virality_score:.4f}")
    print(f"  trending_score  : {score.trending_score:.4f}")
    print()

# Ranking
ranked.sort(key=lambda x: x[1].trending_score, reverse=True)
print("=== RANKING ===")
for i, (label, score) in enumerate(ranked, 1):
    print(f"  #{i} {label} => score: {score.trending_score:.4f}")
