"""
Near-Duplicate Detector — deteksi post yang isinya hampir sama.

Algoritma:
  - Character shingles (k-gram) + Jaccard similarity
  - Sliding window: bandingkan tiap post dengan N post sebelumnya
  - Threshold default 0.85 (85% mirip dianggap duplikat)

Catatan: Phase 5 akan menggantikan ini dengan embedding similarity (pgvector)
untuk akurasi lebih tinggi pada teks parafrase.
"""
import re
import uuid
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _shingles(text: str, k: int = 3) -> set[str]:
    """Buat set character k-gram dari teks."""
    text = text.lower().replace(" ", "")
    if len(text) < k:
        return {text} if text else set()
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def _word_tokens(text: str) -> set[str]:
    """Tokenisasi kata — untuk Jaccard berbasis kata."""
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Hitung Jaccard similarity antara dua set."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


@dataclass
class DuplicateResult:
    post_id: uuid.UUID
    duplicate_of: uuid.UUID
    similarity: float


class NearDuplicateDetector:
    def __init__(
        self,
        threshold: float = 0.85,
        window_size: int = 200,
        use_shingles: bool = True,
        shingle_k: int = 4,
    ):
        self.threshold = threshold
        self.window_size = window_size
        self.use_shingles = use_shingles
        self.shingle_k = shingle_k

    def _fingerprint(self, text: str) -> set[str]:
        if self.use_shingles:
            return _shingles(text, self.shingle_k)
        return _word_tokens(text)

    def find_duplicates(
        self,
        post_ids: list[uuid.UUID],
        contents: list[str],
    ) -> list[DuplicateResult]:
        """
        Deteksi near-duplicate dalam daftar post.

        Args:
            post_ids: list UUID post (urutan sama dengan contents)
            contents: list teks yang sudah dibersihkan

        Returns:
            list DuplicateResult — post yang dianggap duplikat
        """
        if len(post_ids) != len(contents):
            raise ValueError("post_ids dan contents harus panjang sama")

        duplicates: list[DuplicateResult] = []

        # Simpan fingerprint untuk window terakhir
        # Format: [(post_id, fingerprint), ...]
        window: list[tuple[uuid.UUID, set[str]]] = []

        for idx, (pid, content) in enumerate(zip(post_ids, contents)):
            if not content or len(content.strip()) < 20:
                # Teks terlalu pendek untuk dibandingkan — lewati
                window.append((pid, set()))
                continue

            fp = self._fingerprint(content)

            # Bandingkan dengan window sebelumnya
            is_duplicate = False
            best_match_id: uuid.UUID | None = None
            best_similarity = 0.0

            for prev_id, prev_fp in window:
                sim = jaccard_similarity(fp, prev_fp)
                if sim > best_similarity:
                    best_similarity = sim
                    best_match_id = prev_id

            if best_similarity >= self.threshold and best_match_id:
                duplicates.append(
                    DuplicateResult(
                        post_id=pid,
                        duplicate_of=best_match_id,
                        similarity=round(best_similarity, 4),
                    )
                )
                is_duplicate = True

            window.append((pid, fp))

            # Jaga ukuran window
            if len(window) > self.window_size:
                window.pop(0)

        return duplicates


default_detector = NearDuplicateDetector()
