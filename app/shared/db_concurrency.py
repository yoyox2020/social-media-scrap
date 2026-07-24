"""Lock BERSAMA lintas modul (2026-07-24) -- ditemukan live saat test
Twitter (reply-fetching baru menambah volume panggilan rotasi+log
bersamaan): `_ROTATION_DB_LOCK` (third_party_apis/service.py) dan
`_LOG_ACTIVITY_LOCK` (activity_log.py) SEBELUMNYA 2 objek `asyncio.Lock()`
TERPISAH -- keduanya SAMA-SAMA melindungi bagiannya sendiri, TAPI tidak
saling mengunci SATU SAMA LAIN. Coordinator platform mana pun yg
jalankan child paralel via `asyncio.gather()` (Facebook/TikTok/Threads/
News/Twitter) BERBAGI 1 `AsyncSession` yg SAMA di semua child -- kalau
child A sedang `db.commit()` di dalam lock rotasi BERSAMAAN dgn child B
`db.commit()` di dalam lock log_activity, keduanya TETAP bisa bentrok
(`IllegalStateChangeError`) krn dilindungi 2 lock BERBEDA, bukan 1 lock
yg sama.

Fix: SATU lock di sini, dipakai SEMUA titik yg commit/rollback ke
session yg mungkin dibagi child paralel -- third_party_apis/service.py
DAN activity_log.py sekarang import dari sini, BUKAN bikin lock sendiri.
Modul baru (bukan di salah satu file lama) supaya tidak ada dependensi
melingkar (third_party_apis <-> activity_log)."""
from __future__ import annotations

import asyncio

SHARED_SESSION_LOCK = asyncio.Lock()
