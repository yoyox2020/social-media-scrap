"use client";

import { useEffect, useRef } from "react";
import type { TimelineSeries } from "../../lib/trend-api";

const COLORS = ["#e8a33d", "#4fb8c4", "#d97757", "#7ba7d9", "#b48ee0", "#7fbf7f", "#e0a3c9", "#c9c04f"];

interface Props {
  words: string[];
  series: Record<string, TimelineSeries>;
}

// Client Component -- Canvas butuh DOM/browser, jadi ini satu-satunya
// bagian halaman yang perlu "use client". Semua data-nya tetap datang dari
// Server Component induk (page.tsx), bukan fetch ulang di browser.
export function TimelineChart({ words, series }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || words.length === 0) return;

    function draw() {
      const rect = canvas!.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const w = rect.width;
      const h = 300;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      canvas!.style.height = `${h}px`;
      const ctx = canvas!.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);

      const padL = 34, padR = 8, padT = 12, padB = 22;
      const plotW = w - padL - padR, plotH = h - padT - padB;

      const buckets = series[words[0]].total.map((b) => b.bucket);
      const n = buckets.length;
      if (n === 0) return;

      const maxCount = Math.max(
        ...words.flatMap((word) => series[word].total.map((b) => b.count)),
        1
      );

      // gridlines + label sumbu Y
      ctx.strokeStyle = "rgba(128,128,128,0.15)";
      ctx.fillStyle = "#888";
      ctx.font = "10px ui-monospace, monospace";
      ctx.textAlign = "right";
      for (let s = 0; s <= 4; s++) {
        const val = Math.round((maxCount * s) / 4);
        const y = padT + plotH - (plotH * s) / 4;
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(w - padR, y);
        ctx.stroke();
        ctx.fillText(String(val), padL - 6, y + 3);
      }

      // label sumbu X (tiap ~9 titik)
      ctx.textAlign = "center";
      for (let xi = 0; xi < n; xi += Math.max(1, Math.floor(n / 5))) {
        const x = padL + (plotW * xi) / (n - 1 || 1);
        const d = new Date(buckets[xi]);
        ctx.fillText(`${d.getMonth() + 1}/${d.getDate()}`, x, h - 6);
      }

      const px = (i: number) => padL + (plotW * i) / (n - 1 || 1);
      const py = (v: number) => padT + plotH - (plotH * v) / maxCount;

      words.forEach((word, wi) => {
        const vals = series[word].total.map((b) => b.count);
        const color = COLORS[wi % COLORS.length];
        const isTop = wi === 0;

        ctx.beginPath();
        vals.forEach((v, i) => {
          const x = px(i), y = py(v);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = isTop ? 2.2 : 1.3;
        ctx.globalAlpha = isTop ? 1 : 0.8;
        ctx.lineJoin = "round";
        ctx.stroke();
        ctx.globalAlpha = 1;

        // titik di ujung garis (nilai terakhir)
        const lastI = vals.length - 1;
        ctx.beginPath();
        ctx.arc(px(lastI), py(vals[lastI]), isTop ? 3 : 2, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      });
    }

    draw();
    window.addEventListener("resize", draw);
    return () => window.removeEventListener("resize", draw);
  }, [words, series]);

  return (
    <section className="panel">
      <h2>Timeline</h2>
      <p className="panel-sub">mention per hari, kata yang sama dengan Word count</p>
      <canvas ref={canvasRef} className="timeline-canvas" />
      <div className="legend">
        {words.map((w, i) => (
          <span key={w} className="legend-item">
            <span className="legend-swatch" style={{ background: COLORS[i % COLORS.length] }} />
            {w}
          </span>
        ))}
      </div>
    </section>
  );
}
