import re
from collections.abc import Set
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import Settings
from app.models import AnswerChunk, Answerer, Embedder, get_answerer
from app.retrieval import Citation, search

UNKNOWN_ANSWER = "I don't know. I couldn't find this in the indexed sources."
_REFERENCE = re.compile(r"(?m)^[ \t]{0,3}\[[0-9]+\]:[^\r\n]*(?:\r?\n|$)")
_MARKER = re.compile(r"\[([0-9]+)\](?:\([^()\r\n]*\))?")


class KnownAnswer(BaseModel):
    known: Literal[True] = True
    answer: str
    citations: Annotated[list[Citation], Field(min_length=1)]


class UnknownAnswer(BaseModel):
    known: Literal[False] = False
    answer: str = UNKNOWN_ANSWER
    citations: Annotated[list[Citation], Field(max_length=0)] = Field(
        default_factory=list
    )


type AskResult = KnownAnswer | UnknownAnswer


class AskUnavailable(RuntimeError):
    pass


def validate_answer(answer: str, retrieved: list[Citation]) -> AskResult:
    available = {str(c.n): c for c in retrieved}
    cited: set[str] = set()

    def marker(match: re.Match[str]) -> str:
        number = match[1]
        if number not in available:
            return ""
        cited.add(number)
        return f"[{number}]"

    cleaned = _MARKER.sub(marker, _REFERENCE.sub("", answer)).strip()
    if not cited:
        return UnknownAnswer()
    return KnownAnswer(
        answer=cleaned, citations=[c for c in retrieved if str(c.n) in cited]
    )


def ask(
    settings: Settings,
    question: str,
    *,
    source_ids: Set[UUID] | None = None,
    embedder: Embedder | None = None,
    answerer: Answerer | None = None,
) -> AskResult:
    try:
        retrieved = search(settings, question, source_ids=source_ids, embedder=embedder)
        if not retrieved:
            return UnknownAnswer()
        model = answerer if answerer is not None else get_answerer(settings)
        answer = model.answer(
            question.strip(), [AnswerChunk(c.n, c.snippet) for c in retrieved]
        )
    except RuntimeError:
        raise AskUnavailable("Answers are unavailable") from None
    return validate_answer(answer, retrieved)
