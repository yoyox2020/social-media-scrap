import type { TimelineSeries } from "../../lib/trend-api";

const COLORS = ["#e8a33d", "#4fb8c4", "#d97757", "#7ba7d9", "#b48ee0", "#7fbf7f", "#e0a3c9", "#c9c04f"];

interface Props {
  words: string[];
  series: Record<string, TimelineSeries>;
}

// Server Component -- lebar bar dihitung sekali saat render di server,
// tidak butuh JS di browser sama sekali untuk panel ini.
export function WordCountPanel({ words, series }: Props) {
  const max = Math.max(...words.map((w) => series[w].total_mentions), 1);

  return (
    <section className="panel">
      <h2>Word count</h2>
      <p className="panel-sub">total_mentions, diurutkan API</p>
      <div className="wc-list">
        {words.map((word, i) => {
          const count = series[word].total_mentions;
          return (
            <div key={word} className="wc-row">
              <span className="wc-word">{word}</span>
              <span className="wc-track">
                <span
                  className="wc-fill"
                  style={{ width: `${(count / max) * 100}%`, background: COLORS[i % COLORS.length] }}
                />
              </span>
              <span className="wc-count">{count}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
