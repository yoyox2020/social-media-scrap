"""
Test scraping Facebook (post + komentar + sentiment) via Apify Actor
"social-media-sentiment-analysis-tool" (ycQuEFDDZmgX7BAsL).

Requires:
    pip install apify_client
    set APIFY_API_TOKEN di environment (jangan hardcode token di sini)

Cara pakai:
    export APIFY_API_TOKEN="apify_api_..."
    python scripts/apify_facebook_test.py <username> [latest_posts] [latest_comments]

Contoh:
    python scripts/apify_facebook_test.py starbucks 5 3

Catatan penting (lihat docs/apify-instagram-method.md untuk detail, gotcha yang
sama berlaku untuk Facebook):
- latestComments HARUS > 0. Kalau 0, Actor tidak menghasilkan baris output
  sama sekali walau post berhasil di-fetch.
- Field opsional (dateFrom, dateTo, instagramProfileName, dst) jangan diisi
  None — Actor menolak null, cukup dihilangkan dari dict kalau tidak dipakai.
"""
import json
import os
import sys

from apify_client import ApifyClient

ACTOR_ID = "ycQuEFDDZmgX7BAsL"


def run_facebook_test(username: str, latest_posts: int = 5, latest_comments: int = 3) -> list[dict]:
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise SystemExit("Set APIFY_API_TOKEN di environment terlebih dahulu.")

    client = ApifyClient(token)

    run_input = {
        "facebookProfileName": username,
        "scrapeFacebook": True,
        "scrapeInstagram": False,
        "scrapeTiktok": False,
        "sentimentAnalysis": True,
        "latestPosts": latest_posts,
        "latestComments": latest_comments,
    }

    print("Menjalankan Actor dengan input:", run_input)
    run = client.actor(ACTOR_ID).call(run_input=run_input)
    print(f"Run {run.id} -> status {run.status}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "starbucks"
    latest_posts = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    latest_comments = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    items = run_facebook_test(username, latest_posts, latest_comments)

    unique_posts = {i["postUrl"] for i in items}
    print(f"\nTotal item: {len(items)} | post unik: {len(unique_posts)}")

    out_path = f"apify_facebook_{username}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"Hasil disimpan ke {out_path}")


if __name__ == "__main__":
    main()
