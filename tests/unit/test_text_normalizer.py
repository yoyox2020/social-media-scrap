"""Unit tests for TextNormalizer."""
import pytest

from app.services.processing.text_normalizer import TextNormalizer


@pytest.fixture
def normalizer():
    return TextNormalizer(expand_slang=True, remove_stopwords=False)


@pytest.fixture
def normalizer_with_stopword_removal():
    return TextNormalizer(expand_slang=True, remove_stopwords=True)


def test_lowercase(normalizer):
    result = normalizer.normalize("HURUF BESAR Semua")
    assert result == result.lower()


def test_expand_slang_yg(normalizer):
    result = normalizer.normalize("saya yg terbaik")
    assert "yang" in result
    assert "yg" not in result


def test_expand_slang_dgn(normalizer):
    result = normalizer.normalize("pergi dgn teman")
    assert "dengan" in result


def test_expand_slang_tdk(normalizer):
    result = normalizer.normalize("saya tdk suka ini")
    assert "tidak" in result


def test_expand_slang_gak(normalizer):
    result = normalizer.normalize("gak mau ikut")
    assert "tidak" in result


def test_stopword_removal(normalizer_with_stopword_removal):
    result = normalizer_with_stopword_removal.normalize("saya dan kamu pergi ke pasar")
    tokens = result.split()
    # stopwords seperti 'dan', 'ke' harus dihapus
    assert "dan" not in tokens
    assert "ke" not in tokens
    assert "pasar" in tokens


def test_tokenize(normalizer):
    tokens = normalizer.tokenize("Hello World! Ini test.")
    assert "hello" in tokens
    assert "world" in tokens
    assert "ini" in tokens
    assert "test" in tokens


def test_detect_language_indonesian(normalizer):
    text = "saya tidak tahu kenapa dia tidak mau datang ke sini"
    lang = normalizer.detect_language(text)
    assert lang == "id"


def test_detect_language_english(normalizer):
    text = "I do not know why she does not want to come here with us"
    lang = normalizer.detect_language(text)
    assert lang == "en"


def test_detect_language_short_text(normalizer):
    lang = normalizer.detect_language("hi")
    assert lang == "unknown"


def test_detect_language_empty(normalizer):
    lang = normalizer.detect_language("")
    assert lang == "unknown"


def test_normalize_empty(normalizer):
    assert normalizer.normalize("") == ""
