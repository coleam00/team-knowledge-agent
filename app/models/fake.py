"""Offline models with versioned, process-independent behavior."""

import hashlib
import math
import re
from collections.abc import Sequence

from app.models.interfaces import AnswerChunk

_STOP_WORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had "
    "has have how i if in into is it its may might of on or our should so than "
    "that the their them there these they this those to was we were what when "
    "where which who why will with would you your".split()
)


class FakeEmbedder:
    r"""2048 unsigned word-count buckets, normalized to unit length.

    v2 tokenizes casefolded Unicode text with ``\w+`` (including underscores
    and digits), dropping the fixed English _STOP_WORDS set. Each remaining
    UTF-8 token's SHA-256 digest, interpreted big-endian, selects a bucket modulo
    2048. Input without remaining tokens has the zero vector.
    Changing this algorithm requires a new model_id and re-embedding stored data.
    """

    model_id = "fake-bow-sha256-2048-v2"
    dimensions = 2048

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"\w+", text.casefold()):
            if token in _STOP_WORDS:
                continue
            bucket = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest())
            vector[bucket % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class FakeAnswerer:
    """Quote nonblank chunks in supplied order; never interpret their contents.

    Retrieval and relevance filtering belong to the caller. The question cannot
    supply facts or alter the citation markers, and no provider is contacted.
    """

    def answer(self, question: str, chunks: Sequence[AnswerChunk]) -> str:
        quotes = [
            f'"{chunk.text}" [{chunk.n}]' for chunk in chunks if chunk.text.strip()
        ]
        return "\n\n".join(quotes) if quotes else "I don't know."
