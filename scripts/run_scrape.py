"""
Scraping manual — lihat proses step-by-step di terminal.

Usage:
    py scripts/run_scrape.py
    py scripts/run_scrape.py --keyword "demo mahasiswa"
    py scripts/run_scrape.py --keyword "pilkada" --comments
    py scripts/run_scrape.py --keyword "pilkada" --comments --pages 2

Mode ini TIDAK menyimpan ke DB — hanya menampilkan output.
Tambah --save untuk simpan ke DB (butuh DB lokal aktif).
"""
import argparse
import asyncio
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
DIM    = "\033[2m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def step(label: str):
    print(f"\n{BOLD}{CYAN}[{ts()}] ── {label}{RESET}")

def ok(msg: str):
    print(f"  {GREEN}✔{RESET}  {msg}")

def info(msg: str):
    print(f"  {DIM}→{RESET}  {msg}")

def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET}  {msg}")

def err(msg: str):
    print(f"  {RED}✘{RESET}  {msg}")

def divider():
    print(f"  {DIM}{'─' * 60}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Keyword Search
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_videos(keyword: str, depth: int = 1) -> list[dict]:
    import httpx

    token = os.getenv("ENSEMBLE_DATA_API_TOKEN", "")
    base  = os.getenv("ENSEMBLE_DATA_BASE_URL", "https://ensembledata.com/apis")

    step(f"Mencari video YouTube: \"{keyword}\"  (depth={depth})")
    info(f"API: {base}/youtube/search")
    info(f"Token: {token[:8]}…")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/youtube/search",
            params={"keyword": keyword, "depth": depth, "token": token},
        )

    info(f"HTTP {resp.status_code}  ({len(resp.content)} bytes)")

    if resp.status_code != 200:
        err(f"Gagal: {resp.text[:200]}")
        return []

    data  = resp.json().get("data", {})
    posts = data.get("posts", [])
    ok(f"Ditemukan {len(posts)} item dari API")

    videos = []
    for p in posts:
        vr = p.get("videoRenderer") or p.get("richItemRenderer", {}).get("content", {}).get("videoRenderer", {})
        if not vr:
            continue
        video_id    = vr.get("videoId", "")
        title_runs  = (vr.get("title") or {}).get("runs", [])
        title       = "".join(r.get("text", "") for r in title_runs)
        channel_runs = (vr.get("ownerText") or {}).get("runs", [])
        channel     = channel_runs[0].get("text", "") if channel_runs else ""
        views       = (vr.get("viewCountText") or {}).get("simpleText", "")
        published   = (vr.get("publishedTimeText") or {}).get("simpleText", "")
        thumb_list  = (vr.get("thumbnail") or {}).get("thumbnails", [])
        thumb       = thumb_list[-1].get("url", "") if thumb_list else ""
        if not video_id:
            continue
        videos.append({
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "views": views,
            "published": published,
            "url": f"https://youtube.com/watch?v={video_id}",
            "thumbnail": thumb,
        })

    ok(f"{len(videos)} video valid (punya videoId)")
    return videos


def print_video_table(videos: list[dict]):
    divider()
    print(f"  {'#':>3}  {'VIDEO ID':12}  {'VIEWS':>15}  {'PUBLISH':>12}  JUDUL / CHANNEL")
    divider()
    for i, v in enumerate(videos, 1):
        title   = v["title"][:45]
        channel = v["channel"][:30]
        views   = v["views"][:15]
        pub     = v["published"][:12]
        vid     = v["video_id"][:12]
        print(f"  {i:>3}. {vid:12}  {views:>15}  {pub:>12}  {BOLD}{title}{RESET}")
        print(f"       {'':12}  {'':>15}  {'':>12}  {DIM}{channel}{RESET}")
    divider()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Fetch Comments
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_comments(video_id: str, max_comments: int = 20) -> list[dict]:
    import httpx

    token = os.getenv("ENSEMBLE_DATA_API_TOKEN", "")
    base  = os.getenv("ENSEMBLE_DATA_BASE_URL", "https://ensembledata.com/apis")

    step(f"Mengambil komentar: {video_id}  (max={max_comments})")
    info(f"URL: https://youtube.com/watch?v={video_id}")

    comments = []
    cursor   = ""
    page     = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while len(comments) < max_comments:
            page += 1
            info(f"Halaman {page}  cursor={cursor[:30]!r if cursor else '(pertama)'}")

            resp = await client.get(
                f"{base}/youtube/video/comments",
                params={"id": video_id, "cursor": cursor, "token": token},
            )
            info(f"HTTP {resp.status_code}  ({len(resp.content)} bytes)")

            if resp.status_code != 200:
                err(f"Gagal: {resp.text[:200]}")
                break

            data        = resp.json().get("data", {})
            raw         = data.get("comments", [])
            next_cursor = data.get("nextCursor", "")

            ok(f"Halaman {page}: {len(raw)} komentar diterima")

            for item in raw:
                ctr     = item.get("commentThreadRenderer") or {}
                comment = ctr.get("comment") or {}
                props   = comment.get("properties") or {}
                author  = comment.get("author") or {}
                toolbar = comment.get("toolbar") or {}

                content_obj = props.get("content") or {}
                text = content_obj.get("content") or "".join(
                    r.get("text", "") for r in content_obj.get("runs", [])
                )
                if not text:
                    continue

                comments.append({
                    "comment_id": props.get("commentId", ""),
                    "text": text,
                    "author": author.get("displayName", "?"),
                    "likes": toolbar.get("likeCountNotliked", "0"),
                    "published": props.get("publishedTime", ""),
                })

            if not next_cursor or len(comments) >= max_comments:
                break
            cursor = next_cursor

    ok(f"Total komentar diambil: {len(comments)}")
    return comments[:max_comments]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Sentiment Analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_comments(comments: list[dict]) -> list[dict]:
    from app.ai.lexicon.service import analyze

    step(f"Analisis sentimen lexicon — {len(comments)} komentar")

    label_color = {
        "positif": GREEN,
        "negatif": RED,
        "netral":  YELLOW,
    }
    results = []

    for i, c in enumerate(comments, 1):
        text     = c["text"]
        result   = analyze(text)
        color    = label_color.get(result.label, RESET)
        label    = f"{color}{result.label:8s}{RESET}"
        score    = f"{result.score:+.0f}"
        author   = c["author"][:20]
        likes    = c["likes"]
        pub      = c["published"][:15]

        print(f"  {i:>3}. [{label}] skor={score:>4}  @{author:<20} ❤ {likes:<5} {DIM}{pub}{RESET}")
        print(f"       {text[:80]}")
        if result.matched_positive:
            print(f"       {GREEN}+{RESET} {result.matched_positive[:4]}")
        if result.matched_negative:
            print(f"       {RED}-{RESET} {result.matched_negative[:4]}")

        results.append({**c, "label": result.label, "score": result.score})

    return results


def print_summary(keyword: str, videos: list[dict], comments: list[dict], analyzed: list[dict]):
    from collections import Counter

    step("RINGKASAN")
    divider()
    print(f"  Keyword      : {BOLD}{keyword}{RESET}")
    print(f"  Video temuan : {len(videos)}")
    print(f"  Komentar     : {len(comments)}")
    if analyzed:
        counter = Counter(r["label"] for r in analyzed)
        total   = len(analyzed)
        print(f"  Sentimen     :")
        for label in ["positif", "negatif", "netral"]:
            n   = counter.get(label, 0)
            pct = round(n / total * 100, 1) if total else 0
            bar = "█" * int(pct / 5)
            print(f"    {label:8s}: {n:3d} ({pct:5.1f}%)  {bar}")
    divider()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main(keyword: str, fetch_comments_flag: bool, pages: int):
    print(f"\n{BOLD}{'='*64}{RESET}")
    print(f"{BOLD}  SOCIAL MEDIA SCRAPER — Manual Run Mode{RESET}")
    print(f"  Waktu mulai : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{BOLD}{'='*64}{RESET}")

    # 1. Cari video
    videos = await fetch_videos(keyword, depth=pages)
    if not videos:
        err("Tidak ada video ditemukan. Cek token atau keyword.")
        return

    print_video_table(videos)

    if not fetch_comments_flag:
        print_summary(keyword, videos, [], [])
        return

    # 2. Ambil komentar dari video pertama
    target = videos[0]
    print(f"\n  {CYAN}Target video:{RESET} {target['title'][:60]}")
    print(f"  {DIM}{target['url']}{RESET}")

    comments = await fetch_comments(target["video_id"], max_comments=20)
    if not comments:
        warn("Tidak ada komentar atau gagal fetch.")
        print_summary(keyword, videos, [], [])
        return

    # 3. Analisis sentimen
    analyzed = analyze_comments(comments)

    # 4. Ringkasan
    print_summary(keyword, videos, comments, analyzed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run YouTube scraping manual")
    parser.add_argument("--keyword", "-k", default="berita indonesia viral",
                        help="Keyword pencarian (default: 'berita indonesia viral')")
    parser.add_argument("--comments", "-c", action="store_true",
                        help="Ambil komentar dari video pertama + analisis sentimen")
    parser.add_argument("--pages", "-p", type=int, default=1,
                        help="Kedalaman pencarian video (1 = ~20 video, 2 = ~40, dll)")
    args = parser.parse_args()

    asyncio.run(main(args.keyword, args.comments, args.pages))
