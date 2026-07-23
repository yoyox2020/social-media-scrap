"""Scraper TikTok via ADB + uiautomator (2026-07-24, permintaan user).

BUKAN bagian dari app/ (FastAPI/Celery) -- sengaja file TERPISAH, jalan
manual di komputer yang punya adb + HP Android nyata terkolonisasi
kabel/wifi debugging. Cara kerja: buka app TikTok di HP asli, scroll
For You Page (FYP), tiap video di-"uiautomator dump" (ambil accessibility
tree layar saat ini dalam bentuk XML), lalu di-parse buat ambil
username/caption/like/comment/share -- baik lewat resource-id yang
DIKENALI (0Y5, kalau app resource-id nya beda versi/di-obfuscated) MAUPUN
heuristik teks generik (@username, angka+suffix rb/jt/K/M) sbg fallback,
supaya tetap jalan walau TikTok ganti versi/obfuscation.

TIDAK PERNAH dites end-to-end sukses (2026-07-24): app TikTok di HP test
(Redmi A15, Android Go edition) macet di splash screen terus (bukan
crash, bukan bug script -- device itu cuma punya GMS "Go" bukan GMS
penuh, kemungkinan app-nya butuh Play Integrity yang gak ada). Kalau
dipakai di HP lain yang TikTok-nya bisa kebuka normal, alur di bawah ini
seharusnya jalan, tapi WAJIB dicoba --count 1 dulu buat verifikasi
struktur dump sebelum dipakai skala besar (lihat CATATAN PENTING di
bawah).

CATATAN PENTING -- resource-id TikTok berubah-ubah/di-obfuscate per versi
app, jadi RESOURCE_ID_HINTS di bawah adalah TEBAKAN BERBASIS POLA UMUM
(bukan hasil verifikasi live), dan heuristik teks adalah fallback utama
yang seharusnya lebih tahan-versi. Kalau hasil ekstraksi kosong/aneh,
jalankan dengan --debug-dump buat nyimpen 1 file XML dump mentah dan
lihat sendiri struktur asli tag "text"/"content-desc" di situ, lalu
sesuaikan RESOURCE_ID_HINTS atau heuristik EXTRACT_* di bawah.

Contoh pakai:
    python scripts/tiktok_adb_scraper.py --count 20 --output tiktok_fyp.jsonl
    python scripts/tiktok_adb_scraper.py --count 1 --debug-dump  # cek struktur dulu
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TIKTOK_PACKAGES = [
    "com.ss.android.ugc.trill",   # varian internasional/ID yang ditemukan di HP test
    "com.zhiliaoapp.musically",   # varian global lain
]

# Tebakan resource-id umum TikTok Android (BUKAN diverifikasi live, lihat
# docstring). Dicoba duluan; kalau tidak match satupun, fallback ke
# heuristik teks generik (EXTRACT_* di bawah).
RESOURCE_ID_HINTS = {
    "username": ("author", "unique_id", "user_name"),
    "caption": ("desc", "video_desc", "content_desc_text"),
    "like_count": ("digg_count", "like_count", "like"),
    "comment_count": ("comment_count", "comment"),
    "share_count": ("share_count", "share"),
}

NUMBER_SUFFIX_RE = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*(rb|ribu|jt|juta|k|m|b)?$", re.IGNORECASE
)
USERNAME_RE = re.compile(r"^@[\w.]{2,32}$")


def _adb_binary() -> str:
    """Cari adb: PATH dulu, lalu lokasi platform-tools umum di Windows."""
    for candidate in ("adb", r"C:\platform-tools\adb.exe"):
        try:
            subprocess.run([candidate, "version"], capture_output=True, check=True, timeout=5)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(
        "adb tidak ditemukan di PATH maupun C:\\platform-tools\\adb.exe -- "
        "pasang path ke adb manual via --adb-path"
    )


class AdbDevice:
    def __init__(self, adb_path: str, serial: str | None = None):
        self.adb_path = adb_path
        self.serial = serial

    def _base_cmd(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def shell(self, *args: str, timeout: float = 30.0) -> str:
        result = subprocess.run(
            self._base_cmd() + ["shell", *args], capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout

    def pull(self, remote_path: str, local_path: Path, timeout: float = 30.0) -> None:
        subprocess.run(
            self._base_cmd() + ["pull", remote_path, str(local_path)],
            capture_output=True, text=True, timeout=timeout, check=True,
        )

    def current_focus_package(self) -> str | None:
        out = self.shell("dumpsys", "window")
        m = re.search(r"mCurrentFocus=Window\{[^\s]+ u0 ([\w.]+)/", out)
        return m.group(1) if m else None

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe_up(self, width: int = 1080, height: int = 2400, duration_ms: int = 300) -> None:
        """Swipe dari 70% ke 20% tinggi layar -- gerakan standar 'video berikutnya' di FYP."""
        x = width // 2
        y_start = int(height * 0.70)
        y_end = int(height * 0.20)
        self.shell("input", "swipe", str(x), str(y_start), str(x), str(y_end), str(duration_ms))

    def screen_size(self) -> tuple[int, int]:
        out = self.shell("wm", "size")
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1080, 2400  # fallback umum


def find_running_tiktok_package(device: AdbDevice) -> str | None:
    for pkg in TIKTOK_PACKAGES:
        out = device.shell("pm", "list", "packages", pkg)
        if pkg in out:
            return pkg
    return None


def launch_tiktok(device: AdbDevice, package: str) -> None:
    """`monkey` bisa BLOCKING lama (>30s) kalau app yang dibuka hang/lambat
    settle (ditemukan nyata 2026-07-24, app macet di splash) -- timeout
    di sini sengaja longgar (45s) + di-catch, krn kalaupun monkey timeout
    duluan, event "buka app"-nya kemungkinan besar SUDAH terkirim, jadi
    `wait_until_focused()` sesudahnya tetap bisa mendeteksi app kebuka
    beneran atau genuinely gagal."""
    try:
        device.shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=45.0)
    except subprocess.TimeoutExpired:
        print("Peringatan: perintah 'monkey' timeout (app lambat/hang) -- lanjut cek status app.")


def wait_until_focused(device: AdbDevice, package: str, timeout_s: float = 30.0, poll_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        focus = device.current_focus_package()
        if focus == package and "splash" not in (focus or "").lower():
            return True
        time.sleep(poll_s)
    return False


def dump_ui(device: AdbDevice, scratch_dir: Path, tag: str) -> Path | None:
    remote = "/sdcard/tiktok_scraper_dump.xml"
    try:
        device.shell("uiautomator", "dump", remote, timeout=15.0)
    except subprocess.TimeoutExpired:
        return None
    local = scratch_dir / f"dump_{tag}.xml"
    try:
        device.pull(remote, local)
    except subprocess.CalledProcessError:
        return None
    return local if local.exists() and local.stat().st_size > 0 else None


def _parse_number(token: str) -> float | None:
    m = NUMBER_SUFFIX_RE.match(token.strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    suffix = (m.group(2) or "").lower()
    multiplier = {"rb": 1_000, "ribu": 1_000, "k": 1_000, "jt": 1_000_000, "juta": 1_000_000,
                  "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return value * multiplier


def extract_by_resource_id(nodes: list[dict]) -> dict:
    found = {}
    for field, hints in RESOURCE_ID_HINTS.items():
        for node in nodes:
            rid = node.get("resource-id", "").lower()
            if any(hint in rid for hint in hints):
                text = node.get("text") or node.get("content-desc") or ""
                if text:
                    found[field] = text
                    break
    return found


def extract_by_heuristic(nodes: list[dict]) -> dict:
    """Fallback kalau resource-id tidak match apapun -- scan semua teks
    yang keluar dari accessibility tree dan tebak berdasarkan pola."""
    texts = [n.get("text") or n.get("content-desc") or "" for n in nodes]
    texts = [t.strip() for t in texts if t.strip()]

    result: dict = {}
    numeric_hits = []
    for t in texts:
        if USERNAME_RE.match(t) and "username" not in result:
            result["username"] = t
        else:
            num = _parse_number(t)
            if num is not None:
                numeric_hits.append((t, num))

    # 3 angka pertama yang muncul di layar FYP biasanya like/comment/share
    # berurutan (posisi tombol vertikal kanan) -- ASUMSI, verifikasi via
    # --debug-dump kalau meleset.
    labels = ["like_count", "comment_count", "share_count"]
    for label, (raw, _num) in zip(labels, numeric_hits):
        result[label] = raw

    # caption = teks terpanjang yang BUKAN username/angka
    remaining = [t for t in texts if t != result.get("username") and _parse_number(t) is None]
    if remaining:
        result["caption"] = max(remaining, key=len)

    return result


def parse_dump(xml_path: Path) -> dict:
    tree = ET.parse(xml_path)
    nodes = [elem.attrib for elem in tree.getroot().iter("node")]

    data = extract_by_resource_id(nodes)
    missing = [f for f in ("username", "caption", "like_count") if f not in data]
    if missing:
        data = {**extract_by_heuristic(nodes), **data}  # resource-id hasil menang kalau ada

    return data


def run(args: argparse.Namespace) -> None:
    adb_path = args.adb_path or _adb_binary()
    device = AdbDevice(adb_path, serial=args.device)

    package = find_running_tiktok_package(device)
    if not package:
        print("TikTok tidak ditemukan terpasang di HP ini (dicek: %s)" % ", ".join(TIKTOK_PACKAGES))
        sys.exit(1)
    print(f"Pakai package: {package}")

    launch_tiktok(device, package)
    print("Menunggu TikTok keluar dari splash screen...")
    if not wait_until_focused(device, package, timeout_s=args.launch_timeout):
        print(
            "TIDAK berhasil keluar dari splash dlm %ss -- app kemungkinan hang/butuh interaksi manual "
            "(login/consent dialog) atau device tidak kompatibel. Berhenti." % args.launch_timeout
        )
        sys.exit(1)
    print("TikTok terbuka, mulai scraping FYP.")

    width, height = device.screen_size()
    scratch_dir = Path(args.scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)

    seen_captions: set[str] = set()
    collected = 0
    with out_path.open("a", encoding="utf-8") as out_f:
        for i in range(args.count):
            xml_path = dump_ui(device, scratch_dir, tag=str(i))
            if xml_path is None:
                print(f"[{i}] gagal dump UI (idle state/timeout), skip video ini")
            else:
                try:
                    data = parse_dump(xml_path)
                except ET.ParseError:
                    data = {}

                if not args.debug_dump:
                    xml_path.unlink(missing_ok=True)

                caption = data.get("caption", "")
                dedup_key = caption or data.get("username", "") or str(i)
                if dedup_key not in seen_captions:
                    seen_captions.add(dedup_key)
                    record = {
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                        "index": i,
                        **data,
                    }
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_f.flush()
                    collected += 1
                    print(f"[{i}] {data.get('username', '?')}: {caption[:60] or '(caption kosong)'}")
                else:
                    print(f"[{i}] duplikat video sebelumnya (belum sempat scroll?), skip simpan")

            if i < args.count - 1:
                device.swipe_up(width, height)
                time.sleep(args.interval)

    print(f"Selesai. {collected} video unik tersimpan ke {out_path}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scrape FYP TikTok via ADB+uiautomator dari HP Android nyata.")
    p.add_argument("--count", type=int, default=20, help="Jumlah video yang di-scroll (default 20)")
    p.add_argument("--output", default="tiktok_fyp_dump.jsonl", help="File output JSONL (append)")
    p.add_argument("--interval", type=float, default=2.5, help="Jeda detik antar-scroll (default 2.5)")
    p.add_argument("--launch-timeout", type=float, default=30.0, help="Detik max tunggu app keluar dari splash")
    p.add_argument("--device", default=None, help="Serial adb kalau lebih dari 1 HP terkoneksi")
    p.add_argument("--adb-path", default=None, help="Path manual ke adb.exe kalau tidak ada di PATH")
    p.add_argument("--scratch-dir", default="scripts/.tiktok_scraper_scratch", help="Folder temp buat file dump XML")
    p.add_argument("--debug-dump", action="store_true", help="Simpan semua file dump XML mentah (buat debug struktur)")
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
