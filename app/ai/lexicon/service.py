"""
Lexicon-based sentiment analyzer untuk Bahasa Indonesia.

Algoritma:
  1. Lowercase + tokenisasi (split kata, hapus tanda baca)
  2. Identifikasi dan hapus stopwords
  3. Deteksi negasi (tidak, bukan, tak, kurang) — balik polaritas kata sesudahnya
  4. Cocokkan tiap token dengan leksikon positif/negatif
  5. score = len(matched_positive) - len(matched_negative)
  6. label: positif (>0), negatif (<0), netral (=0)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"

_NEGATION_WORDS = {
    # formal
    "tidak", "bukan", "tak", "kurang", "tanpa", "jangan", "belum",
    # informal / gaul
    "ga", "gak", "ngga", "nggak", "enggak", "kagak", "gakk",
}


@dataclass
class LexiconResult:
    matched_positive: list[str] = field(default_factory=list)
    matched_negative: list[str] = field(default_factory=list)
    removed_stopwords: list[str] = field(default_factory=list)
    score: float = 0.0
    label: str = "netral"


@lru_cache(maxsize=1)
def _load_words(filename: str) -> frozenset[str]:
    path = _DATA_DIR / filename
    if not path.exists():
        return frozenset()
    words: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower()
        if w:
            words.add(w)
    return frozenset(words)


def _positive() -> frozenset[str]:
    return _load_words("positive.txt")


def _negative() -> frozenset[str]:
    return _load_words("negative.txt")


def _stopwords() -> frozenset[str]:
    return _load_words("stopwords.txt")


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return [t for t in text.split() if t]


def analyze(text: str) -> LexiconResult:
    """
    Analisis sentimen satu teks menggunakan leksikon.

    Returns:
        LexiconResult dengan detail kata cocok, stopwords, skor, dan label.
    """
    if not text or not text.strip():
        return LexiconResult()

    pos_set = _positive()
    neg_set = _negative()
    stop_set = _stopwords()

    tokens = _tokenize(text)
    result = LexiconResult()

    pos_count = 0
    neg_count = 0
    negated = False

    for i, token in enumerate(tokens):
        # Negasi dicek SEBELUM stopword — "tidak" harus tetap aktif sebagai negator
        if token in _NEGATION_WORDS:
            negated = True
            continue

        if token in stop_set:
            result.removed_stopwords.append(token)
            # Negasi TIDAK direset oleh stopword — "tidak pula bagus" tetap negatif
            continue

        if token in pos_set:
            if negated:
                result.matched_negative.append(f"!{token}")
                neg_count += 1
            else:
                result.matched_positive.append(token)
                pos_count += 1
            negated = False
            continue

        if token in neg_set:
            if negated:
                result.matched_positive.append(f"!{token}")
                pos_count += 1
            else:
                result.matched_negative.append(token)
                neg_count += 1
            negated = False
            continue

        negated = False

    result.score = float(pos_count - neg_count)
    if result.score > 0:
        result.label = "positif"
    elif result.score < 0:
        result.label = "negatif"
    else:
        result.label = "netral"

    return result


def analyze_batch(texts: list[str]) -> list[LexiconResult]:
    return [analyze(t) for t in texts]


def reload_lexicon() -> None:
    """Paksa reload semua file leksikon (clear lru_cache)."""
    _load_words.cache_clear()
