"""Shared model contracts, independent of retrieval and provider transports."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AnswerChunk:
    """Retrieved source text and its caller-assigned, 1-based citation marker."""

    n: int
    text: str

    def __post_init__(self) -> None:
        if type(self.n) is not int or self.n < 1:
            raise ValueError("Citation markers must be positive integers")


class Embedder(Protocol):
    @property
    def model_id(self) -> str:
        """Stable identity for storing and comparing compatible embeddings."""
        ...

    def embed(self, text: str) -> list[float]:
        """Return a vector for one document or query."""
        ...


class Answerer(Protocol):
    def answer(self, question: str, chunks: Sequence[AnswerChunk]) -> str:
        r"""Use bare [n] citations; escape literal \, [, ], ` and ~ with a backslash.

        The shared validator decodes escaped text without treating it as references.
        """
        ...
