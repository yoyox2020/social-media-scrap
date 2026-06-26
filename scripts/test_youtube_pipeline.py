"""
Quick test script — jalankan standalone tanpa DB/Celery.

Usage:
    cd social-media-scrap
    py scripts/test_youtube_pipeline.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Test 1: Google Trends ─────────────────────────────────────────────────────

def test_google_trends():
    print("\n=== TEST 1: Google Trends (tanpa token) ===")
    from app.integrations.google_trends.connector import fetch_trending

    result = fetch_trending(geo="ID", period="24h", limit=5)
    print(f"Waktu fetch : {result.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}")
    for item in result.items:
        print(f"  {item.rank}. {item.title} [{item.traffic}]")
    assert len(result.items) > 0, "Tidak ada trending items"
    print("PASS [OK]")
    return result.keywords


# ── Test 2: Lexicon Sentiment ─────────────────────────────────────────────────

def test_lexicon_sentiment():
    print("\n=== TEST 2: Lexicon Sentiment (tanpa token) ===")
    from app.ai.lexicon.service import analyze

    cases = [
        # formal
        ("Produk ini sangat bagus dan memuaskan!", "positif"),
        ("Kualitas buruk, sangat mengecewakan dan tidak berguna", "negatif"),
        ("Produk yang biasa saja", "netral"),
        ("Tidak bagus sama sekali", "negatif"),
        ("Bukan yang terbaik tapi lumayan", "negatif"),
        # informal / slang YouTube
        ("Gak punya malu banget sih", "negatif"),
        ("Astaghfirullah kelakuannya", "negatif"),
        ("josss mantul keren abis", "positif"),
        ("Gokil banget ini konten, salut!", "positif"),
        ("Ga berguna sama sekali, nyebelin!", "negatif"),
        ("Alhamdulillah akhirnya sukses juga", "positif"),
        ("Udah Di iwik diobok tiap hari siang mlm", "negatif"),
        ("wkwk ngakak banget seru parah", "positif"),
        ("toxic banget si orang itu, nyinyir terus", "negatif"),
        ("Subhanallah, indah sekali", "positif"),
    ]

    failed = 0
    for text, expected in cases:
        r = analyze(text)
        ok = r.label == expected
        if not ok:
            failed += 1
        status = "OK  " if ok else "FAIL"
        pos_words = r.matched_positive[:3]
        neg_words = r.matched_negative[:3]
        print(f"  [{status}] label={r.label:8s} score={r.score:+.0f} | {text[:50]}")
        if pos_words:
            print(f"          +: {pos_words}")
        if neg_words:
            print(f"          -: {neg_words}")

    if failed == 0:
        print("PASS [OK] semua kasus benar")
    else:
        print(f"PARTIAL [{len(cases)-failed}/{len(cases)} benar]")


# ── Test 3: YouTube Keyword Search ───────────────────────────────────────────

def test_youtube_keyword_search(token: str):
    print("\n=== TEST 3: YouTube Keyword Search ===")
    import httpx

    keyword = "berita indonesia viral"
    url = "https://ensembledata.com/apis/youtube/search"
    resp = httpx.get(url, params={"keyword": keyword, "depth": 1, "token": token}, timeout=20)
    print(f"  Status  : {resp.status_code}")

    if resp.status_code != 200:
        print(f"  SKIP: {resp.text[:200]}")
        return []

    data = resp.json()
    posts = data.get("data", {}).get("posts", [])
    print(f"  Posts   : {len(posts)}")

    extracted = []
    for p in posts[:3]:
        vr = p.get("videoRenderer") or {}
        if not vr:
            continue
        video_id = vr.get("videoId", "")
        title_runs = (vr.get("title") or {}).get("runs", [])
        title = "".join(r.get("text", "") for r in title_runs)
        channel_runs = (vr.get("ownerText") or {}).get("runs", [])
        channel = channel_runs[0].get("text", "") if channel_runs else ""
        views = (vr.get("viewCountText") or {}).get("simpleText", "")
        published = (vr.get("publishedTimeText") or {}).get("simpleText", "")
        print(f"  Video   : [{video_id}]")
        print(f"    Judul  : {title[:70]}")
        print(f"    Channel: {channel}")
        print(f"    Views  : {views}  |  {published}")
        extracted.append(vr)

    print(f"PASS [OK] — {len(posts)} video ditemukan")
    return extracted


# ── Test 4: YouTube Comments + Lexicon ───────────────────────────────────────

def test_youtube_comments_and_sentiment(token: str, video_id: str = "dQw4w9WgXcQ"):
    print(f"\n=== TEST 4: YouTube Comments + Lexicon (video={video_id}) ===")
    import httpx
    from app.ai.lexicon.service import analyze

    resp = httpx.get(
        "https://ensembledata.com/apis/youtube/video/comments",
        params={"id": video_id, "cursor": "", "token": token},
        timeout=20,
    )
    print(f"  Status  : {resp.status_code}")

    if resp.status_code != 200:
        print(f"  SKIP: {resp.text[:200]}")
        return

    data = resp.json().get("data", {})
    raw_comments = data.get("comments", [])
    next_cursor = data.get("nextCursor", "")
    print(f"  Komentar: {len(raw_comments)}")
    print(f"  Cursor  : {next_cursor[:50] if next_cursor else 'None (halaman terakhir)'}")

    # Parse komentar
    print("\n  --- 5 Komentar + Sentimen ---")
    for item in raw_comments[:5]:
        ctr = item.get("commentThreadRenderer") or {}
        comment = ctr.get("comment") or {}
        props = comment.get("properties") or {}
        author = comment.get("author") or {}
        toolbar = comment.get("toolbar") or {}

        comment_id = props.get("commentId", "")
        content_obj = props.get("content") or {}
        text = content_obj.get("content") or "".join(
            r.get("text", "") for r in content_obj.get("runs", [])
        )
        display_name = author.get("displayName", "?")
        likes = toolbar.get("likeCountNotliked", "0")
        published = props.get("publishedTime", "")

        if not text:
            continue

        sentiment = analyze(text)
        print(f"  [{sentiment.label:8s}] score={sentiment.score:+.0f} | @{display_name} | {likes} likes | {published}")
        print(f"    Komentar: {text[:80]}")
        if sentiment.matched_positive:
            print(f"    +kata  : {sentiment.matched_positive[:3]}")
        if sentiment.matched_negative:
            print(f"    -kata  : {sentiment.matched_negative[:3]}")

    print("PASS [OK]")


# ── Test 5: Distribusi Sentimen dari batch komentar ───────────────────────────

def test_sentiment_distribution(token: str, video_id: str = "dQw4w9WgXcQ"):
    print(f"\n=== TEST 5: Distribusi Sentimen — {video_id} ===")
    import httpx
    from collections import Counter
    from app.ai.lexicon.service import analyze

    resp = httpx.get(
        "https://ensembledata.com/apis/youtube/video/comments",
        params={"id": video_id, "cursor": "", "token": token},
        timeout=20,
    )
    if resp.status_code != 200:
        print("  SKIP")
        return

    raw_comments = resp.json().get("data", {}).get("comments", [])
    labels = []
    for item in raw_comments:
        ctr = item.get("commentThreadRenderer") or {}
        comment = ctr.get("comment") or {}
        props = comment.get("properties") or {}
        content_obj = props.get("content") or {}
        text = content_obj.get("content") or "".join(
            r.get("text", "") for r in content_obj.get("runs", [])
        )
        if text:
            labels.append(analyze(text).label)

    total = len(labels)
    counter = Counter(labels)
    print(f"  Total dianalisis: {total}")
    for label in ["positif", "negatif", "netral"]:
        count = counter.get(label, 0)
        pct = round(count / total * 100, 1) if total else 0
        bar = "#" * int(pct / 5)
        print(f"  {label:8s}: {count:3d} ({pct:5.1f}%)  {bar}")
    print("PASS [OK]")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    token = os.getenv("ENSEMBLE_DATA_API_TOKEN", "")

    # Test 1 & 2: tidak butuh token
    trending_keywords = test_google_trends()
    test_lexicon_sentiment()

    if not token:
        print("\n[SKIP] Test 3-5 — ENSEMBLE_DATA_API_TOKEN belum diset di .env")
        sys.exit(0)

    # Test 3: keyword search — ambil video_id dari hasil pertama
    videos = test_youtube_keyword_search(token)
    video_id = videos[0].get("videoId", "dQw4w9WgXcQ") if videos else "dQw4w9WgXcQ"

    # Test 4: komentar + lexicon per komentar
    test_youtube_comments_and_sentiment(token, video_id)

    # Test 5: distribusi sentimen
    test_sentiment_distribution(token, video_id)

    print("\n" + "="*60)
    print("SEMUA TEST SELESAI")
    print("="*60)
