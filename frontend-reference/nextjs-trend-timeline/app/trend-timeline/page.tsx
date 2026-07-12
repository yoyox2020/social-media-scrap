import { fetchTrendTimeline } from "../../lib/trend-api";
import { WordCountPanel } from "./WordCountPanel";
import { TimelineChart } from "./TimelineChart";
import "./styles.css";

export const revalidate = 300; // refresh data tiap 5 menit (ISR)

export default async function TrendTimelinePage() {
  // `keywords` SENGAJA tidak diisi -- ini yang memicu auto-discover di API.
  // Kata yang tampil di halaman ini murni hasil deteksi otomatis dari data
  // (posts.content, semua platform), bukan daftar yang di-hardcode di sini.
  const data = await fetchTrendTimeline({
    dateFrom: "2026-06-01",
    dateTo: "2026-07-10",
    topN: 8,
  });

  const words = data.keywords;

  return (
    <main className="trend-page">
      <header className="trend-header">
        <p className="eyebrow">Trend Discovery &middot; auto-discover</p>
        <h1>Word count &amp; timeline</h1>
        <p className="meta">
          {data.date_from} &rarr; {data.date_to} &middot; mode {data.mode} &middot; {words.length} kata
        </p>
      </header>

      {words.length === 0 ? (
        <p className="empty">Tidak ada kata trending ditemukan di rentang tanggal ini.</p>
      ) : (
        <div className="trend-grid">
          <WordCountPanel words={words} series={data.series} />
          <TimelineChart words={words} series={data.series} />
        </div>
      )}
    </main>
  );
}
