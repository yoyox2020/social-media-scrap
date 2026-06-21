"""
Text Cleaner — membersihkan teks raw dari social media sebelum masuk ke NLP pipeline.

Pipeline urutan:
  1. Decode HTML entities  (&amp; → &)
  2. Strip HTML tags       (<br> → spasi)
  3. Hapus URLs
  4. Hapus mention & hashtag (opsional)
  5. Hapus emoji
  6. Normalisasi whitespace
"""
import html
import re


# ── Regex patterns (dikompilasi sekali) ────────────────────────────────────────

_HTML_TAGS = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MENTION = re.compile(r"@\w+")
_HASHTAG_SYMBOL = re.compile(r"#(\w+)")          # simpan kata, hapus #
_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002700-\U000027BF"   # dingbats
    "\U000024C2-\U0001F251"
    "\U0001f926-\U0001f937"
    "\U00010000-\U0010ffff"
    "♀-♂"
    "☀-⭕"
    "‍⏏⏩⌚️〰"
    "]+",
    flags=re.UNICODE,
)
_MULTI_WHITESPACE = re.compile(r"\s+")
_SPECIAL_CHARS = re.compile(r"[^\w\s\.\,\!\?\-\:\;\'\"\(\)]", re.UNICODE)


class TextCleaner:
    def __init__(
        self,
        remove_urls: bool = True,
        remove_mentions: bool = True,
        expand_hashtags: bool = True,
        remove_emoji: bool = True,
        remove_special_chars: bool = False,
    ):
        self.remove_urls = remove_urls
        self.remove_mentions = remove_mentions
        self.expand_hashtags = expand_hashtags
        self.remove_emoji = remove_emoji
        self.remove_special_chars = remove_special_chars

    def clean(self, text: str | None) -> str:
        if not text:
            return ""

        # 1. Decode HTML entities
        text = html.unescape(text)

        # 2. Strip HTML tags
        text = _HTML_TAGS.sub(" ", text)

        # 3. Hapus URL
        if self.remove_urls:
            text = _URL.sub("", text)

        # 4. Hapus mention (@user)
        if self.remove_mentions:
            text = _MENTION.sub("", text)

        # 5. Expand hashtag (#python → python) atau hapus
        if self.expand_hashtags:
            text = _HASHTAG_SYMBOL.sub(r"\1", text)
        else:
            text = _HASHTAG_SYMBOL.sub("", text)

        # 6. Hapus emoji
        if self.remove_emoji:
            text = _EMOJI.sub("", text)

        # 7. Hapus karakter spesial non-alfanumerik (opsional)
        if self.remove_special_chars:
            text = _SPECIAL_CHARS.sub(" ", text)

        # 8. Normalisasi whitespace
        text = _MULTI_WHITESPACE.sub(" ", text).strip()

        return text

    def clean_batch(self, texts: list[str | None]) -> list[str]:
        return [self.clean(t) for t in texts]


# Singleton default cleaner
default_cleaner = TextCleaner()
