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
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // berhenti polling otomatis setelah 5 menit

type ViewState =
  | { phase: "idle" }
  | { phase: "searching" }
  | { phase: "needs_confirmation"; data: TopicSearchResponse["data"] }
  | { phase: "queued"; topicId: string; queuedKeywords: string[] }
  | { phase: "done"; totalPosts: number; results: PostItem[] }
  | { phase: "error"; message: string };

export function TopicSearchBox({ authToken }: { authToken: string }) {
  const [topicName, setTopicName] = useState("");
  const [keywordInput, setKeywordInput] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [state, setState] = useState<ViewState>({ phase: "idle" });
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

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
    if (!topicName.trim() || keywords.length === 0) return;
    setState({ phase: "searching" });
    try {
      const res = await searchTopics(authToken, {
        topics: [{ name: topicName, keywords }],
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
        topics: [{ name: topicName, keywords }],
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

  // Langkah 5: polling GET /search/topics/{id} sampai semua keyword yang
  // di-queue sudah punya data, atau timeout.
  function startPolling(topicId: string, queuedKeywords: string[]) {
    const startedAt = Date.now();
    pollTimer.current = setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        if (pollTimer.current) clearInterval(pollTimer.current);
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
        <p>
          Sedang dicari satu-per-satu di background untuk: {state.queuedKeywords.join(", ")}. Halaman
          ini otomatis diperbarui setiap {POLL_INTERVAL_MS / 1000} detik.
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
