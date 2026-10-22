import re
from collections.abc import Set
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import Settings
from app.models import AnswerChunk, Answerer, Embedder, get_answerer
from app.retrieval import Citation, search

UNKNOWN_ANSWER = "I don't know. I couldn't find this in the indexed sources."
_REFERENCE = re.compile(r"(?m)^[ \t]{0,3}\[(?:\\.|[^\]\\\r\n])+\]:[^\r\n]*(?:\r?\n|$)")
_MARKER = re.compile(r"\[([0-9]+)\]")
_REFERENCE_LABEL = re.compile(r"\[(?:\\.|[^\]\\\r\n])*\]")


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


def citation_link_end(text: str, start: int) -> int | None:
    if text[start : start + 1] == "[":
        label = _REFERENCE_LABEL.match(text, start)
        return label.end() if label else None
    if text[start : start + 1] != "(":
        return start
    depth = 1
    quoted_until = ""
    position = start + 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if quoted_until:
            if char == quoted_until:
                quoted_until = ""
        elif char == "<":
            quoted_until = ">"
        elif char in "\"'" and text[position - 1].isspace():
            quoted_until = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def validate_answer(answer: str, retrieved: list[Citation]) -> AskResult:
    available = {str(c.n): c for c in retrieved}
    cited: set[str] = set()
    text = _REFERENCE.sub("", answer)
    parts: list[str] = []
    position = 0
    while match := _MARKER.search(text, position):
        parts.append(text[position : match.start()])
        end = citation_link_end(text, match.end())
        if end is None:
            return UnknownAnswer()
        number = match[1]
        if number in available:
            cited.add(number)
            parts.append(f"[{number}]")
        position = end
    parts.append(text[position:])
    if not cited:
        return UnknownAnswer()
    return KnownAnswer(
        answer="".join(parts).strip(),
        citations=[c for c in retrieved if str(c.n) in cited],
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
