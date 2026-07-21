"""
Script sederhana: ambil "Word count" trending — kata yang paling sering
disebut di posts (lintas platform, termasuk News) pada rentang tanggal
tertentu — dari GET /api/v1/trend-discovery/timeline (mode auto-discover,
lihat docs/trend-discovery-api.md).

Ini BUKAN endpoint baru — cuma memanggil API yang sudah ada dan menampilkan
field `total_mentions` tiap kata sebagai tabel ranking sederhana (mirip
widget "Word count" di tool social-listening pada umumnya). Kalau butuh
data timeline (per jam/hari), field `total` di response API yang sama
sudah berisi itu — script ini sengaja cuma tampilkan ringkasannya.

Requires:
    pip install requests

Cara pakai — DUA cara login, pilih salah satu:

    # 1) Token siap pakai (dari POST /auth/login manual sebelumnya)
    python scripts/word_count_trending.py --token <ACCESS_TOKEN> \
        --date-from 2026-06-01 --date-to 2026-07-10 --top-n 10

    # 2) Auto-login pakai email+password (script yang urus ambil token-nya)
    python scripts/word_count_trending.py --email you@example.com --password ***** \
        --date-from 2026-06-01 --date-to 2026-07-10 --top-n 10

    # Filter opsional ke satu platform saja:
    python scripts/word_count_trending.py --token <ACCESS_TOKEN> \
        --date-from 2026-06-01 --date-to 2026-07-10 --platform tiktok
"""
from __future__ import annotations

import argparse
import getpass
import os

import requests

API_BASE_URL = os.environ.get("TREND_API_BASE_URL", "http://187.77.125.10:8000")


def login(email: str, password: str) -> str:
    """POST /auth/login, return access_token. Raise kalau gagal (email/password
    salah, dst) -- pesan errornya diteruskan apa adanya dari API."""
    resp = requests.post(
        f"{API_BASE_URL}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


def get_word_count(
    token: str,
    date_from: str,
    date_to: str,
    top_n: int = 10,
    platform: str | None = None,
) -> list[tuple[str, int]]:
    """Panggil GET /trend-discovery/timeline (mode auto-discover, keywords
    kosong) lalu ambil `total_mentions` tiap kata -- itu SUDAH persis
    "word count" yang dimaksud, diurutkan dari yang paling sering disebut."""
    params = {"date_from": date_from, "date_to": date_to, "top_n": top_n, "interval": "day"}
    if platform:
        params["platform"] = platform

    resp = requests.get(
        f"{API_BASE_URL}/api/v1/trend-discovery/timeline",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    series = resp.json()["data"]["series"]

    word_counts = [(word, info["total_mentions"]) for word, info in series.items()]
    word_counts.sort(key=lambda item: item[1], reverse=True)
    return word_counts

 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ambil word count trending dari /trend-discovery/timeline")
    parser.add_argument("--token", default=None, help="Access token (JWT) siap pakai -- kalau kosong, pakai --email/--password")
    parser.add_argument("--email", default=None, help="Email login -- alternatif dari --token, script yang auto-login")
    parser.add_argument("--password", default=None, help="Password login -- kalau kosong tapi --email diisi, akan ditanya interaktif (tersembunyi)")
    parser.add_argument("--date-from", required=True, help="YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--platform", default=None, help="instagram/facebook/tiktok/twitter/youtube/news, kosong = semua")
    args = parser.parse_args()

    if args.token:
        token = args.token
    elif args.email:
        password = args.password or getpass.getpass("Password: ")
        token = login(args.email, password)
    else:
        parser.error("wajib isi --token ATAU --email (+ --password)")

    results = get_word_count(token, args.date_from, args.date_to, args.top_n, args.platform)

    print(f"\nWord count - {args.date_from} s/d {args.date_to}"
          + (f" ({args.platform})" if args.platform else " (semua platform)") + "\n")
    if not results:
        print("Tidak ada data ditemukan di rentang tanggal ini.")
    for word, count in results:
        print(f"  {word:<30s} {count}")
    print()
