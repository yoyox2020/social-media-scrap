"""
Threads connector — wraps EnsembleData Threads endpoints.

Diverifikasi LIVE 2026-07-19 (bukan cuma baca dokumentasi) -- kode
SEBELUMNYA (param `keyword`, path `/threads/post/info-replies`, bentuk
respons `data.threads`/`data.posts`) TERBUKTI SALAH SEMUA saat dites ke API
asli, tidak pernah benar-benar dipakai sebelumnya. Detail temuan live:

1. Search (`/threads/keyword/search`): param wajib `name` (BUKAN `keyword`).
   Bentuk respons: `{"data": [{"node": {"thread": {"thread_items":
   [{"post": {...}}]}}, "cursor": ""}]}`. Field `cursor` SELALU kosong di
   semua sample yang dites -- endpoint ini KEMUNGKINAN BESAR tidak
   mendukung pagination sama sekali (tidak ada cara pasti minta "halaman
   berikutnya"), TIDAK seperti Instagram/TikTok/Reddit di project ini.

2. Balasan (path asli `/threads/post/replies`, BUKAN `/threads/post/
   info-replies` yang tertulis di ThreadsEndpoints -- itu 404, tidak
   pernah ada di API asli): param wajib `id` (pk dari post, angka string).
   Bentuk respons beda dari search: `{"data": [{"node": {"thread_type":
   "thread", "thread_items": [{"post": {...}}, ...]}, "cursor": ""}]}`
   (thread_items LANGSUNG di node, TANPA lapisan `.thread` seperti search).
   Item PERTAMA di `data[]` SELALU post ASLINYA (is_reply=False) -- WAJIB
   dilewati saat ekstrak balasan. Beberapa entry punya >1 thread_items
   (balasan-ke-balasan / nested reply chain) -- HARUS di-flatten semua.

   **KETERBATASAN PENTING (belum terverifikasi tuntas, kuota EnsembleData
   habis di tengah pengujian)**: 1x panggilan endpoint ini TERBUKTI cuma
   balikin SEBAGIAN balasan (contoh nyata: post dengan
   `direct_reply_count=58` cuma dapat ~28 balasan dari 1 panggilan, TIDAK
   ADA cursor utk lanjut). Parameter `depth` (dipakai endpoint YouTube/
   Instagram lain di EnsembleData) BELUM sempat diuji apakah menambah
   cakupan -- kuota habis sebelum hasil didapat. TERIMA best-effort:
   kirim `depth` kalau diminta, tapi JANGAN asumsikan semua balasan pasti
   didapat -- laporan hasil harus jujur soal ini (lihat `total_replies_hint`
   vs jumlah yang benar2 tersimpan).
"""
from typing import Any

from app.integrations.ensemble_data.client import EnsembleDataClient

PLATFORM = "threads"

# Path REPLIES asli hasil verifikasi live -- BEDA dari ThreadsEndpoints.
# POST_INFO_REPLIES ("/threads/post/info-replies", TERBUKTI 404 saat
# dites). Didefinisikan di sini (bukan endpoints.py) sampai
# ThreadsEndpoints ikut diperbaiki.
_REPLIES_PATH = "/threads/post/replies"


class ThreadsConnector:
    def __init__(self, client: EnsembleDataClient):
        self.client = client

    async def search_by_keyword(self, keyword: str, cursor: str | None = None) -> dict[str, Any]:
        """Cari post Threads berdasarkan keyword.

        CATATAN: param API asli adalah `name`, BUKAN `keyword` (dites live
        2026-07-19 -- `keyword` balikin HTTP 422 "field required: name").
        `cursor` dikirim best-effort kalau diisi TAPI belum terbukti
        endpoint ini benar2 memakainya (lihat catatan modul)."""
        params: dict[str, Any] = {"name": keyword}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get("/threads/keyword/search", params=params)

    async def get_user_posts(self, username: str, cursor: str | None = None) -> dict[str, Any]:
        """Ambil post dari username Threads. BELUM diverifikasi live (beda
        endpoint dari yang sudah dites 2026-07-19) -- path/param bisa saja
        masih salah, perlu tes ulang sebelum dipakai produksi."""
        params: dict[str, Any] = {"username": username}
        if cursor:
            params["cursor"] = cursor
        return await self.client.get("/threads/user/posts", params=params)

    async def get_post_replies(self, post_pk: str, depth: int | None = None) -> dict[str, Any]:
        """Ambil balasan/komentar untuk satu post Threads (by `pk`, ANGKA
        string dari post, BUKAN `code`/shortcode -- keduanya dites live,
        cuma `pk` yang valid utk param `id` endpoint ini).

        `depth` best-effort (BELUM terverifikasi menambah cakupan balasan,
        lihat catatan modul) -- dikirim kalau diisi, tapi kalau tidak
        berpengaruh, 1 panggilan tetap cuma dapat SEBAGIAN balasan pada
        post yang repliesnya banyak (keterbatasan API pihak ketiga,
        BUKAN bug di kode kita)."""
        params: dict[str, Any] = {"id": post_pk}
        if depth:
            params["depth"] = depth
        return await self.client.get(_REPLIES_PATH, params=params)

    def extract_cursor(self, raw: dict[str, Any]) -> str | None:
        """SELALU None di semua sample yang dites live 2026-07-19 (field
        `cursor` per-item kosong) -- dipertahankan sbg best-effort kalau
        EnsembleData memperbaiki ini di masa depan, BUKAN diandalkan."""
        data = raw.get("data") or []
        for item in reversed(data):
            c = item.get("cursor")
            if c:
                return c
        return None

    def extract_posts(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Ekstrak list `post` dari respons SEARCH -- bentuk asli (live
        2026-07-19): `data[].node.thread.thread_items[].post`."""
        posts: list[dict[str, Any]] = []
        for item in raw.get("data") or []:
            thread = ((item.get("node") or {}).get("thread")) or {}
            for ti in thread.get("thread_items") or []:
                post = ti.get("post")
                if post:
                    posts.append(post)
        return posts

    def extract_replies(self, raw: dict[str, Any], root_post_pk: str) -> list[dict[str, Any]]:
        """Ekstrak list `post` (balasan) dari respons REPLIES -- bentuk
        asli BEDA dari search: `data[].node.thread_items[].post` (TANPA
        lapisan `.thread`). Item dengan pk == root_post_pk (post asli,
        BUKAN balasan) DIBUANG -- selalu muncul sebagai entry pertama tapi
        difilter by pk (lebih aman drpd asumsi posisi) supaya balasan yg
        kebetulan me-repost/quote post aslinya tidak ikut salah kebuang."""
        replies: list[dict[str, Any]] = []
        for item in raw.get("data") or []:
            node = item.get("node") or {}
            for ti in node.get("thread_items") or []:
                post = ti.get("post")
                if post and str(post.get("pk")) != str(root_post_pk):
                    replies.append(post)
        return replies
