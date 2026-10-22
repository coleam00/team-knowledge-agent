"""Answer syntax: escaped literals and code spans are data; bare [n] are references."""

import re
from collections.abc import Iterator

_ESCAPABLE = "\\[]`~"
_CONTAINERS = re.compile(r"[ \t]*(?:(?:>|[-+*][ \t]+|[0-9]+[.)][ \t]+)[ \t]*)*")
_DEFINITION = re.compile(r"\[(?:\\.|[^\]\\\r\n])+\]:[ \t]*(.*)")
_MARKER = re.compile(r"\[([0-9]+)\]")
_REFERENCE_LABEL = re.compile(r"\[(?:\\.|[^\]\\\r\n])*\]")


def escape_literal(text: str) -> str:
    return "".join("\\" + char if char in _ESCAPABLE else char for char in text)


def container_content(line: str) -> str:
    return _CONTAINERS.sub("", line, count=1)


def line_at(text: str, start: int) -> tuple[str, int]:
    end = text.find("\n", start)
    end = len(text) if end < 0 else end + 1
    return container_content(text[start:end]).rstrip("\r\n"), end


def closing_delimiter(text: str, start: int, delimiter: str) -> int | None:
    position = start
    while position < len(text):
        if text[position] == "\\":
            position += 2
        elif text[position] == delimiter:
            return position + 1
        else:
            position += 1
    return None


def definition_end(text: str, start: int) -> int | None:
    line, end = line_at(text, start)
    definition = _DEFINITION.fullmatch(line)
    if definition is None:
        return None
    destination = definition[1]
    if not destination and end < len(text):
        destination, end = line_at(text, end)
    if destination.startswith("<"):
        destination_end = closing_delimiter(destination, 1, ">")
        title = destination[destination_end:].lstrip() if destination_end else ""
    else:
        parts = destination.split(maxsplit=1)
        title = parts[1] if len(parts) == 2 else ""
    if not title and end < len(text):
        following, following_end = line_at(text, end)
        if following.startswith(('"', "'", "(")):
            title, end = following, following_end
    if title.startswith(('"', "'", "(")):
        delimiter = ")" if title[0] == "(" else title[0]
        remainder = title[1:]
        while closing_delimiter(remainder, 0, delimiter) is None and end < len(text):
            remainder, end = line_at(text, end)
    return end


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


def code_end(text: str, start: int) -> int | None:
    char = text[start]
    if char not in "`~":
        return None
    end = start + 1
    while end < len(text) and text[end] == char:
        end += 1
    delimiter = text[start:end]
    if char == "~" and len(delimiter) < 3:
        return None
    closing = re.search(rf"(?<!{char}){re.escape(delimiter)}(?!{char})", text[end:])
    if closing:
        return end + closing.end()
    return len(text) if len(delimiter) >= 3 else None


def answer_tokens(text: str) -> Iterator[tuple[str, str | None]]:
    """Yield literal text or a citation number; malformed links fail closed."""
    position = 0
    while position < len(text):
        if position == 0 or text[position - 1] == "\n":
            end = definition_end(text, position)
            if end is not None:
                position = end
                continue
        if (
            text[position] == "\\"
            and position + 1 < len(text)
            and text[position + 1] in _ESCAPABLE
        ):
            yield text[position + 1], None
            position += 2
            continue
        end = code_end(text, position)
        if end is not None:
            yield text[position:end], None
            position = end
            continue
        if match := _MARKER.match(text, position):
            end = citation_link_end(text, match.end())
            if end is None:
                raise ValueError("Incomplete citation link")
            yield match[0], match[1]
            position = end
            continue
        yield text[position], None
        position += 1
