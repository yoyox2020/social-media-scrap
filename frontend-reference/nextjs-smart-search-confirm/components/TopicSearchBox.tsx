"use client";

/**
 * Contoh implementasi alur konfirmasi + verifikasi Smart Search:
 *
 * 1. User isi nama topik + keyword, klik "Cari".
 * 2. Request PERTAMA dikirim TANPA confirm_third_party -- backend
 *    verifikasi ke database. Kalau ada keyword yang kosong, backend balas
 *    status "needs_confirmation", TIDAK ada panggilan third-party sama sekali.
 * 3. Komponen tampilkan dialog "Data X tidak ditemukan, cari ke sumber luar?".
 * 4. User klik "Ya" -> request KEDUA dikirim, PERSIS SAMA payload-nya,
 *    cuma confirm_third_party: true. Backend verifikasi ULANG ke database
 *    dulu (siapa tahu sudah ada dari proses lain) -- baru kalau BENAR-BENAR
 *    masih kosong, keyword itu didaftarkan ke antrian background & backend
 *    langsung balas status "queued" (tidak menunggu hasil pencarian).
 * 5. Selama status "queued", komponen polling GET /search/topics/{id}
 *    tiap beberapa detik sampai semua keyword yang di-queue sudah ketemu
 *    (atau timeout).
 */

import { useEffect, useRef, useState } from "react";
import {
  getTopicDetail,
  searchTopics,
  type PostItem,
  type TopicSearchResponse,
} from "../lib/smart-search-api";

const POLL_INTERVAL_MS = 8000;
// Batas maksimal BROWSER menunggu -- ini BUKAN batas proses di server (server
// tetap jalan sampai tuntas terlepas dari ini, lihat FLOW.md poin 5). 2 menit
// sudah kasih buffer wajar drpd platform paling lambat yg teruji (News/
// Firecrawl ~90 detik).
const POLL_TIMEOUT_MS = 2 * 60 * 1000;

type ViewState =
  | { phase: "idle" }
  | { phase: "searching" }
  | { phase: "needs_confirmation"; data: TopicSearchResponse["data"] }
  | { phase: "queued"; topicId: string; queuedKeywords: string[] }
  | { phase: "done"; totalPosts: number; results: PostItem[] }
  // "stopped" -- polling berhenti TANPA hasil (timeout ATAU user klik
  // "Hentikan"), beda dari "queued" (masih aktif menunggu) supaya UI tidak
  // nyangkut selamanya kelihatan "masih jalan" padahal backend sudah selesai
  // (bisa saja hasilnya genuinely nol, itu BUKAN error).
  | { phase: "stopped"; topicId: string }
  | { phase: "error"; message: string };

export function TopicSearchBox({ authToken }: { authToken: string }) {
  const [topicName, setTopicName] = useState("");
  const [keywordInput, setKeywordInput] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [state, setState] = useState<ViewState>({ phase: "idle" });
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // Keyword yang benar2 dipakai di request pertama -- dipakai ulang PERSIS
  // SAMA di request konfirmasi kedua, supaya tidak mismatch kalau user
  // sempat menambah keyword lain di kolom input sebelum klik "Ya".
  const lastSearchedKeywords = useRef<string[]>([]);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  function addKeyword() {
    const kw = keywordInput.trim();
    if (kw && !keywords.includes(kw)) setKeywords([...keywords, kw]);
    setKeywordInput("");
  }

  function removeKeyword(kw: string) {
    setKeywords(keywords.filter((k) => k !== kw));
  }

  // Langkah 1+2: kirim pencarian, TANPA confirm_third_party dulu.
  async function handleSearch() {
    // Kalau user ketik keyword tapi lupa/belum tekan Enter, masukkan
    // otomatis -- sebelumnya di sini langsung `return` diam-diam kalau
    // keywords[] masih kosong, tombol jadi terlihat "tidak bereaksi".
    const pending = keywordInput.trim();
    const effectiveKeywords = pending && !keywords.includes(pending) ? [...keywords, pending] : keywords;
    if (pending) {
      setKeywords(effectiveKeywords);
      setKeywordInput("");
    }
    if (!topicName.trim() || effectiveKeywords.length === 0) return;

    lastSearchedKeywords.current = effectiveKeywords;
    setState({ phase: "searching" });
    try {
      const res = await searchTopics(authToken, {
        topics: [{ name: topicName, keywords: effectiveKeywords }],
        // platforms sengaja tidak dikirim -> otomatis SEMUA platform
        save_topic: true,
        confirm_third_party: false,
      });

      const topic = res.data.topics[0];
      if (res.data.needs_confirmation_keywords.length > 0) {
        setState({ phase: "needs_confirmation", data: res.data });
      } else {
        setState({ phase: "done", totalPosts: topic.total_posts, results: topic.results });
      }
    } catch (err) {
      setState({ phase: "error", message: (err as Error).message });
    }
  }

  // Langkah 4: user klik "Ya" -> kirim ULANG payload yang SAMA PERSIS,
  // cuma confirm_third_party: true.
  async function handleConfirm() {
    setState({ phase: "searching" });
    try {
      const res = await searchTopics(authToken, {
        topics: [{ name: topicName, keywords: lastSearchedKeywords.current }],
        save_topic: true,
        confirm_third_party: true,
      });

      const topic = res.data.topics[0];
      if (res.data.queued_keywords.length > 0 && topic.topic_id) {
        setState({ phase: "queued", topicId: topic.topic_id, queuedKeywords: res.data.queued_keywords });
        startPolling(topic.topic_id, res.data.queued_keywords);
      } else {
        // Ternyata sudah ketemu duluan (verifikasi ulang menemukan data
        // baru) -- tidak jadi ada yang diantrikan sama sekali.
        setState({ phase: "done", totalPosts: topic.total_posts, results: topic.results });
      }
    } catch (err) {
      setState({ phase: "error", message: (err as Error).message });
    }
  }

  function handleCancel() {
    setState({ phase: "idle" });
  }

  // Tombol "Hentikan" -- berhenti MEMANTAU dari sisi browser. TIDAK
  // membatalkan proses di server (server tidak punya cara dibatalkan paksa
  // di tengah jalan, dan biasanya sudah keburu selesai/hampir selesai krn
  // tiap item cuma perlu 8-90 detik) -- ini cuma menghentikan browser
  // supaya berhenti bertanya-tanya terus.
  function handleStopWatching(topicId: string) {
    if (pollTimer.current) clearInterval(pollTimer.current);
    setState({ phase: "stopped", topicId });
  }

  // Langkah 5: polling GET /search/topics/{id} sampai semua keyword yang
  // di-queue sudah punya data, atau timeout.
  function startPolling(topicId: string, queuedKeywords: string[]) {
    const startedAt = Date.now();
    pollTimer.current = setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        if (pollTimer.current) clearInterval(pollTimer.current);
        // Timeout browser TIDAK berarti pencarian di server gagal -- bisa
        // saja masih jalan (jarang, krn tiap item biasanya <90 detik) atau
        // sudah selesai dgn hasil nol (genuinely tidak ketemu, bukan error).
        setState({ phase: "stopped", topicId });
        return;
      }
      try {
        const detail = await getTopicDetail(authToken, topicId);
        const stillEmpty = detail.data.keyword_details.filter(
          (k) => queuedKeywords.includes(k.keyword) && k.total_posts === 0
        );
        if (stillEmpty.length === 0) {
          if (pollTimer.current) clearInterval(pollTimer.current);
          const allResults = detail.data.keyword_details.flatMap((k) => k.posts);
          setState({ phase: "done", totalPosts: detail.data.total_posts, results: allResults });
        }
      } catch {
        // biarkan polling lanjut, satu kegagalan network tidak perlu menghentikan siklus
      }
    }, POLL_INTERVAL_MS);
  }

  return (
    <div>
      <input
        value={topicName}
        onChange={(e) => setTopicName(e.target.value)}
        placeholder="Nama topik, mis. Riset Kemacetan Jakarta"
        disabled={state.phase === "searching" || state.phase === "queued"}
      />

      <div>
        <input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addKeyword()}
          placeholder="Ketik keyword lalu tekan Enter..."
          disabled={state.phase === "searching" || state.phase === "queued"}
        />
        {keywords.map((kw) => (
          <span key={kw} onClick={() => removeKeyword(kw)}>
            {kw} ×
          </span>
        ))}
      </div>

      <button onClick={handleSearch} disabled={state.phase === "searching" || state.phase === "queued"}>
        Cari
      </button>

      {state.phase === "searching" && <p>Mencari...</p>}

      {state.phase === "needs_confirmation" && (
        <div>
          <p>
            Data untuk keyword <b>{state.data.needs_confirmation_keywords.join(", ")}</b> tidak
            ditemukan di database. Cari ke sumber luar (Facebook/Instagram/TikTok/Twitter/YouTube/berita)?
          </p>
          <button onClick={handleConfirm}>Ya, cari</button>
          <button onClick={handleCancel}>Batal</button>
        </div>
      )}

      {state.phase === "queued" && (
        <div>
          <p>
            Sedang dicari satu-per-satu di background untuk: {state.queuedKeywords.join(", ")}. Halaman
            ini otomatis diperbarui setiap {POLL_INTERVAL_MS / 1000} detik.
          </p>
          <button onClick={() => handleStopWatching(state.topicId)}>Hentikan pemantauan</button>
        </div>
      )}

      {state.phase === "stopped" && (
        <p>
          Berhenti memantau. Proses pencarian di server (kalau masih berjalan) tetap tuntas sendiri --
          panggil GET /search/topics/{state.topicId} lagi nanti untuk lihat hasilnya kalau ada.
        </p>
      )}

      {state.phase === "done" && (
        <div>
          <p>Ditemukan {state.totalPosts} post.</p>
          <ul>
            {state.results.map((post) => (
              <li key={post.id}>
                [{post.platform}] {post.title.slice(0, 80)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.phase === "error" && <p>Terjadi kesalahan: {state.message}</p>}
    </div>
  );
}
