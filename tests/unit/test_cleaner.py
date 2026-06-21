"""Unit tests for TextCleaner."""
import pytest

from app.services.processing.cleaner import TextCleaner


@pytest.fixture
def cleaner():
    return TextCleaner(
        remove_urls=True,
        remove_mentions=True,
        expand_hashtags=True,
        remove_emoji=True,
    )


def test_clean_html_tags(cleaner):
    result = cleaner.clean("<b>Hello</b> <br> World")
    assert "<b>" not in result
    assert "<br>" not in result
    assert "Hello" in result
    assert "World" in result


def test_clean_html_entities(cleaner):
    result = cleaner.clean("AT&amp;T adalah perusahaan &lt;besar&gt;")
    assert "&amp;" not in result
    assert "AT&T" in result


def test_clean_urls(cleaner):
    result = cleaner.clean("Kunjungi https://example.com untuk info lebih")
    assert "https://example.com" not in result
    assert "Kunjungi" in result
    assert "info lebih" in result


def test_clean_mentions(cleaner):
    result = cleaner.clean("Halo @username kamu keren banget")
    assert "@username" not in result
    assert "kamu keren banget" in result


def test_expand_hashtag(cleaner):
    result = cleaner.clean("Saya suka #python dan #programming")
    assert "#python" not in result
    assert "python" in result
    assert "programming" in result


def test_clean_emoji(cleaner):
    result = cleaner.clean("Halo 😊 semuanya 🎉 selamat datang")
    assert "😊" not in result
    assert "🎉" not in result
    assert "Halo" in result
    assert "selamat datang" in result


def test_normalize_whitespace(cleaner):
    result = cleaner.clean("terlalu   banyak    spasi   di   sini")
    assert "  " not in result


def test_empty_string(cleaner):
    assert cleaner.clean("") == ""
    assert cleaner.clean(None) == ""


def test_clean_batch(cleaner):
    texts = ["Hello <b>World</b>", "Suka #python", None]
    results = cleaner.clean_batch(texts)
    assert len(results) == 3
    assert "<b>" not in results[0]
    assert "#python" not in results[1]
    assert results[2] == ""


def test_plain_text_unchanged(cleaner):
    text = "Ini adalah kalimat biasa tanpa tag atau URL"
    result = cleaner.clean(text)
    assert result == text
