"""
Text Normalizer — normalisasi teks untuk input NLP/AI model.

Pipeline:
  1. Lowercase
  2. Ekspansi singkatan bahasa Indonesia (slang dict)
  3. Hapus stopwords (Indonesia + Inggris)
  4. Tokenisasi
  5. Detect bahasa (heuristik)
"""
import re

# ── Singkatan/slang bahasa Indonesia yang umum ─────────────────────────────────

INDO_SLANG: dict[str, str] = {
    "yg": "yang",
    "dgn": "dengan",
    "utk": "untuk",
    "krn": "karena",
    "sdh": "sudah",
    "blm": "belum",
    "jg": "juga",
    "tp": "tapi",
    "tdk": "tidak",
    "ga": "tidak",
    "gak": "tidak",
    "nggak": "tidak",
    "ngga": "tidak",
    "bgt": "banget",
    "aja": "saja",
    "sm": "sama",
    "bs": "bisa",
    "gw": "saya",
    "gue": "saya",
    "lo": "kamu",
    "lu": "kamu",
    "sy": "saya",
    "km": "kamu",
    "emg": "memang",
    "emang": "memang",
    "klo": "kalau",
    "kl": "kalau",
    "kalo": "kalau",
    "krg": "kurang",
    "lg": "lagi",
    "lgi": "lagi",
    "jdi": "jadi",
    "skrg": "sekarang",
    "skrang": "sekarang",
    "dr": "dari",
    "ke": "ke",
    "pd": "pada",
    "spy": "supaya",
    "ttg": "tentang",
    "bkn": "bukan",
    "gmn": "bagaimana",
    "gimana": "bagaimana",
    "knp": "kenapa",
    "knpa": "kenapa",
    "hrs": "harus",
    "msh": "masih",
    "spt": "seperti",
    "sprt": "seperti",
    "dpt": "dapat",
    "jgn": "jangan",
    "mnrt": "menurut",
    "byk": "banyak",
    "sdkt": "sedikit",
    "mksd": "maksud",
    "kpd": "kepada",
    "thd": "terhadap",
    "tsb": "tersebut",
}

# ── Stopwords ─────────────────────────────────────────────────────────────────

INDONESIAN_STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "ini", "itu", "dengan", "adalah",
    "pada", "untuk", "tidak", "dalam", "akan", "ada", "juga", "saya",
    "kamu", "dia", "kami", "kita", "mereka", "sudah", "bisa", "lebih",
    "satu", "dua", "tiga", "atau", "karena", "kalau", "tapi", "jadi",
    "oleh", "bagi", "atas", "antara", "setelah", "sebelum", "saat",
    "ketika", "seperti", "tentang", "terhadap", "kepada", "tersebut",
    "anda", "beliau", "ia", "nya", "pun", "lah", "kah", "kok", "sih",
    "deh", "dong", "loh", "ya", "yah", "oh", "ah", "eh",
}

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their", "this",
    "that", "these", "those", "what", "which", "who", "when", "where",
    "why", "how", "not", "so", "if", "then", "than", "also", "just",
}

ALL_STOPWORDS = INDONESIAN_STOPWORDS | ENGLISH_STOPWORDS

# ── Language indicator words ───────────────────────────────────────────────────

_INDO_INDICATORS = {
    "yang", "dan", "dengan", "untuk", "tidak", "adalah", "dalam", "pada",
    "sudah", "akan", "ada", "juga", "ini", "itu", "saya", "kami", "kita",
    "mereka", "bisa", "dari", "ke", "di", "karena", "tapi", "kalau",
    "atau", "lebih", "jadi", "oleh", "bagi", "belum", "sudah",
}

_TOKEN_RE = re.compile(r"\b\w+\b", re.UNICODE)


class TextNormalizer:
    def __init__(
        self,
        expand_slang: bool = True,
        remove_stopwords: bool = False,  # default off agar model AI punya konteks penuh
    ):
        self.expand_slang = expand_slang
        self.remove_stopwords = remove_stopwords

    def normalize(self, text: str) -> str:
        """Normalisasi teks untuk NLP. Return teks bersih siap masuk model."""
        if not text:
            return ""

        text = text.lower()

        if self.expand_slang:
            tokens = text.split()
            tokens = [INDO_SLANG.get(t, t) for t in tokens]
            text = " ".join(tokens)

        if self.remove_stopwords:
            tokens = text.split()
            tokens = [t for t in tokens if t not in ALL_STOPWORDS]
            text = " ".join(tokens)

        return text.strip()

    def tokenize(self, text: str) -> list[str]:
        """Tokenisasi sederhana — return list kata."""
        return _TOKEN_RE.findall(text.lower())

    def detect_language(self, text: str) -> str:
        """
        Deteksi bahasa dengan heuristik.
        Return: 'id' (Indonesia), 'en' (English), atau 'unknown'.
        """
        if not text or len(text.strip()) < 10:
            return "unknown"

        tokens = set(_TOKEN_RE.findall(text.lower()))
        if not tokens:
            return "unknown"

        indo_hits = len(tokens & _INDO_INDICATORS)
        ratio = indo_hits / len(tokens)

        if ratio >= 0.12:
            return "id"
        if ratio >= 0.04:
            return "id"  # mostly Indonesian with mixed English

        english_hits = len(tokens & ENGLISH_STOPWORDS)
        if english_hits / len(tokens) >= 0.15:
            return "en"

        return "unknown"


default_normalizer = TextNormalizer()
