"""PDF report generator menggunakan ReportLab."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.reports.schemas import ReportData

# ── Warna tema ─────────────────────────────────────────────────────────────────
PRIMARY = colors.HexColor("#1e3a5f")
ACCENT = colors.HexColor("#2e86de")
POSITIVE_COLOR = colors.HexColor("#27ae60")
NEGATIVE_COLOR = colors.HexColor("#e74c3c")
NEUTRAL_COLOR = colors.HexColor("#95a5a6")
LIGHT_BG = colors.HexColor("#f8f9fa")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontSize=22,
            textColor=PRIMARY,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=colors.grey,
            spaceAfter=12,
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontSize=13,
            textColor=PRIMARY,
            spaceBefore=16,
            spaceAfter=6,
            borderPad=2,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=9,
            leading=13,
            spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "Quote",
            parent=base["Normal"],
            fontSize=8,
            leading=11,
            leftIndent=12,
            textColor=colors.HexColor("#555555"),
            borderPad=4,
        ),
        "stat_label": ParagraphStyle(
            "StatLabel",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.grey,
        ),
        "stat_value": ParagraphStyle(
            "StatValue",
            parent=base["Normal"],
            fontSize=16,
            textColor=PRIMARY,
            spaceAfter=2,
        ),
    }


def _bar(pct: float, width: int = 30) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


class PDFReportGenerator:
    def generate(self, data: ReportData, output_dir: str) -> str:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        file_path = str(Path(output_dir) / f"{data.report_id}.pdf")

        doc = SimpleDocTemplate(
            file_path,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2 * cm,
        )

        s = _styles()
        story = []

        # ── Cover ───────────────────────────────────────────────────────────────
        story.append(Paragraph(data.title, s["title"]))
        story.append(Paragraph(
            f"Keyword: <b>{data.keyword_text}</b> &nbsp;|&nbsp; "
            f"Dibuat: {data.generated_at.strftime('%d %b %Y %H:%M')} UTC &nbsp;|&nbsp; "
            f"Periode: {data.period}",
            s["subtitle"],
        ))
        story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=12))

        # ── Ringkasan Post ──────────────────────────────────────────────────────
        story.append(Paragraph("Ringkasan Data", s["heading"]))

        stat_data = [
            ["Total Post", "Sudah Diproses", "Near-Duplicate"],
            [
                str(data.total_posts),
                str(data.processed_posts),
                str(data.near_duplicates),
            ],
        ]
        stat_table = Table(stat_data, colWidths=[5 * cm, 5 * cm, 5 * cm])
        stat_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, 1), 18),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 1), (-1, 1), ACCENT),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, 1), [LIGHT_BG]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(stat_table)
        story.append(Spacer(1, 8))

        # Language breakdown
        if data.language_breakdown:
            lang_text = "  |  ".join(
                f"<b>{lang.upper()}</b>: {cnt}" for lang, cnt in data.language_breakdown.items()
            )
            story.append(Paragraph(f"Bahasa: {lang_text}", s["body"]))

        # ── Sentimen ────────────────────────────────────────────────────────────
        story.append(Paragraph("Analisis Sentimen", s["heading"]))

        if data.sentiment.total_analyzed == 0:
            story.append(Paragraph("Belum ada data sentimen.", s["body"]))
        else:
            label_colors = {
                "positive": POSITIVE_COLOR,
                "negative": NEGATIVE_COLOR,
                "neutral": NEUTRAL_COLOR,
            }
            for label in ["positive", "negative", "neutral"]:
                cnt = data.sentiment.distribution.get(label, 0)
                pct = data.sentiment.percentages.get(label, 0.0)
                bar = _bar(pct)
                color = label_colors.get(label, colors.grey)
                story.append(Paragraph(
                    f"<font color='#{_hex(color)}'><b>{label.capitalize():<10}</b></font> "
                    f"{bar} {pct:.1f}%  ({cnt} post)",
                    ParagraphStyle(
                        "bar",
                        fontName="Courier",
                        fontSize=8,
                        leading=13,
                        spaceAfter=2,
                    ),
                ))

            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Sentimen dominan: <b>{data.sentiment.dominant.capitalize()}</b> "
                f"dari {data.sentiment.total_analyzed} post yang dianalisis.",
                s["body"],
            ))

            # Contoh post
            if data.sentiment.examples:
                story.append(Spacer(1, 6))
                story.append(Paragraph("Contoh post per sentimen:", s["body"]))
                for ex in data.sentiment.examples:
                    color = label_colors.get(ex["label"], colors.grey)
                    snippet = ex["content"][:180] + ("…" if len(ex["content"]) > 180 else "")
                    story.append(Paragraph(
                        f"[<font color='#{_hex(color)}'><b>{ex['label'].upper()}</b></font>] "
                        f"<i>{ex['platform']}</i> — {snippet}",
                        s["quote"],
                    ))

        # ── Entitas ─────────────────────────────────────────────────────────────
        story.append(Paragraph("Entitas Terdeteksi", s["heading"]))

        if not data.entities.by_type:
            story.append(Paragraph("Belum ada data entitas.", s["body"]))
        else:
            story.append(Paragraph(
                f"Total entitas unik: <b>{data.entities.total_unique}</b>",
                s["body"],
            ))
            for etype, items in data.entities.by_type.items():
                if not items:
                    continue
                story.append(Paragraph(f"<b>{etype}</b>", s["body"]))
                rows = [["Entitas", "Jumlah"]] + [
                    [item["text"], str(item["count"])] for item in items[:8]
                ]
                ent_table = Table(rows, colWidths=[11 * cm, 3 * cm])
                ent_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(ent_table)
                story.append(Spacer(1, 4))

        # ── Tren ────────────────────────────────────────────────────────────────
        story.append(Paragraph("Tren Aktivitas", s["heading"]))

        if not data.trend.volume:
            story.append(Paragraph("Belum ada data tren.", s["body"]))
        else:
            story.append(Paragraph(
                f"Arah tren: <b>{data.trend.direction.upper()}</b> — "
                f"Total {data.trend.total_posts} post.",
                s["body"],
            ))

            if data.trend.platform_breakdown:
                plat_text = "  |  ".join(
                    f"<b>{p}</b>: {c}" for p, c in data.trend.platform_breakdown.items()
                )
                story.append(Paragraph(f"Platform: {plat_text}", s["body"]))

            # Volume table — tampilkan max 10 baris
            vol_rows = [["Periode", "Platform", "Jumlah Post"]]
            for v in data.trend.volume[:10]:
                vol_rows.append([v["period"][:10], v["platform"], str(v["count"])])
            vol_table = Table(vol_rows, colWidths=[5 * cm, 4 * cm, 5 * cm])
            vol_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(vol_table)

        # ── Sample Posts ────────────────────────────────────────────────────────
        if data.top_posts:
            story.append(Paragraph("Contoh Post Terbaru", s["heading"]))
            for i, post in enumerate(data.top_posts, 1):
                label_info = ""
                if post.get("sentiment_label"):
                    label_info = f" [{post['sentiment_label'].upper()}]"
                story.append(Paragraph(
                    f"<b>{i}. {post.get('platform', '').upper()}{label_info}</b> "
                    f"— {post.get('author', 'unknown')} — {post.get('published_at', '')[:10]}",
                    s["body"],
                ))
                content_snippet = (post.get("content") or "")[:250]
                story.append(Paragraph(content_snippet, s["quote"]))
                story.append(Spacer(1, 4))

        # ── Footer ──────────────────────────────────────────────────────────────
        story.append(Spacer(1, 12))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Paragraph(
            "Social Intelligence Platform — Laporan ini dibuat otomatis oleh sistem AI.",
            ParagraphStyle("footer", fontSize=7, textColor=colors.grey, spaceAfter=0),
        ))

        doc.build(story)
        return file_path


def _hex(color) -> str:
    """ReportLab color → hex string tanpa '#'."""
    r = int(color.red * 255)
    g = int(color.green * 255)
    b = int(color.blue * 255)
    return f"{r:02x}{g:02x}{b:02x}"
