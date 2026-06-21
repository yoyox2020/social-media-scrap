"""Unit tests for NearDuplicateDetector."""
import uuid

import pytest

from app.services.processing.deduplicator import (
    NearDuplicateDetector,
    jaccard_similarity,
)


def make_id():
    return uuid.uuid4()


# ── jaccard_similarity ─────────────────────────────────────────────────────────

def test_jaccard_identical():
    s = {"a", "b", "c"}
    assert jaccard_similarity(s, s) == 1.0


def test_jaccard_disjoint():
    assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    sim = jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
    assert 0 < sim < 1


def test_jaccard_empty():
    assert jaccard_similarity(set(), {"a"}) == 0.0
    assert jaccard_similarity({"a"}, set()) == 0.0


# ── NearDuplicateDetector ──────────────────────────────────────────────────────

@pytest.fixture
def detector():
    return NearDuplicateDetector(threshold=0.80, window_size=10)


def test_no_duplicates(detector):
    ids = [make_id(), make_id(), make_id()]
    texts = [
        "produk ini sangat bagus dan berkualitas tinggi",
        "cuaca hari ini sangat cerah dan menyenangkan",
        "harga saham naik drastis pada penutupan perdagangan",
    ]
    result = detector.find_duplicates(ids, texts)
    assert result == []


def test_near_duplicate_detected(detector):
    ids = [make_id(), make_id()]
    # Teks kedua hampir sama dengan pertama (ubah satu kata saja)
    texts = [
        "produk ini sangat bagus dan berkualitas tinggi dan memuaskan pelanggan",
        "produk ini sangat bagus dan berkualitas tinggi dan memuaskan pelanggan kami",
    ]
    result = detector.find_duplicates(ids, texts)
    assert len(result) == 1
    assert result[0].post_id == ids[1]
    assert result[0].similarity >= 0.80


def test_short_text_skipped(detector):
    ids = [make_id(), make_id()]
    texts = ["ok", "ok"]  # terlalu pendek (<20 char)
    result = detector.find_duplicates(ids, texts)
    assert result == []


def test_mismatched_lengths(detector):
    with pytest.raises(ValueError):
        detector.find_duplicates([make_id()], ["text1", "text2"])


def test_exact_duplicate(detector):
    long_text = "ini adalah konten yang sama persis diulang berkali kali tanpa perubahan apapun"
    ids = [make_id(), make_id()]
    result = detector.find_duplicates(ids, [long_text, long_text])
    assert len(result) == 1
    assert result[0].similarity == 1.0


def test_window_size_limit():
    det = NearDuplicateDetector(threshold=0.85, window_size=2)
    ids = [make_id() for _ in range(5)]
    base = "konten yang sama persis dan panjang agar bisa dibandingkan dengan benar"
    texts = [base] * 5
    # Window 2: post ke-3,4,5 hanya dibandingkan dengan 2 post sebelumnya
    result = det.find_duplicates(ids, texts)
    assert len(result) == 4  # semua setelah pertama dianggap duplikat
