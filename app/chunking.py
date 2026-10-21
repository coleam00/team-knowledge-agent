"""Pure source chunking with LF-based, 1-based inclusive line ranges."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

_HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


@dataclass(frozen=True)
class SourceChunk:
    path: str
    start_line: int
    end_line: int
    content: str


def _physical_lines(text: str) -> list[str]:
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _markdown_sections(lines: list[str]) -> Iterator[tuple[int, int]]:
    start = 0
    fence = ""
    for index, line in enumerate(lines):
        body = line.removesuffix("\n")
        if line.endswith("\n"):
            body = body.removesuffix("\r")
        marker = _FENCE.fullmatch(body)
        if fence:
            if marker:
                run, remainder = marker.groups()
                if (
                    run[0] == fence[0]
                    and len(run) >= len(fence)
                    and not remainder.strip(" \t")
                ):
                    fence = ""
        elif marker:
            run, remainder = marker.groups()
            if run[0] == "~" or "`" not in remainder:
                fence = run
        elif _HEADING.match(body):
            if index > start:
                yield start, index
            start = index
    if start < len(lines):
        yield start, len(lines)


def _windows(
    path: str,
    lines: list[str],
    start: int,
    stop: int,
    window_lines: int,
    overlap_lines: int,
) -> Iterator[SourceChunk]:
    while start < stop:
        end = min(start + window_lines, stop)
        yield SourceChunk(path, start + 1, end, "".join(lines[start:end]))
        if end == stop:
            break
        start = end - overlap_lines


def chunk_text(
    path: str,
    text: str,
    *,
    kind: Literal["text", "markdown"],
    window_lines: int = 80,
    overlap_lines: int = 10,
) -> list[SourceChunk]:
    """Preserve exact input text; Markdown overlap stays within ATX sections.

    See docs/chunking.md for the supported heading/fence grammar and provisional
    size defaults. Callers must preserve newlines when decoding source files.
    """
    if kind not in ("text", "markdown"):
        raise ValueError("kind must be 'text' or 'markdown'")
    if type(window_lines) is not int or window_lines <= 0:
        raise ValueError("window_lines must be a positive integer")
    if type(overlap_lines) is not int or not 0 <= overlap_lines < window_lines:
        raise ValueError("overlap_lines must be an integer in [0, window_lines)")
    lines = _physical_lines(text)
    sections = (
        _markdown_sections(lines) if kind == "markdown" else iter([(0, len(lines))])
    )
    return [
        chunk
        for start, stop in sections
        for chunk in _windows(path, lines, start, stop, window_lines, overlap_lines)
    ]
