import random
from typing import Literal

import pytest

from app.chunking import SourceChunk, chunk_text


def ranges(chunks: list[SourceChunk]) -> list[tuple[int, int]]:
    return [(chunk.start_line, chunk.end_line) for chunk in chunks]


def test_nine_lines_have_exact_overlapping_windows() -> None:
    assert chunk_text(
        "src/code.py",
        "1\n2\n3\n4\n5\n6\n7\n8\n9",
        kind="text",
        window_lines=4,
        overlap_lines=1,
    ) == [
        SourceChunk("src/code.py", 1, 4, "1\n2\n3\n4\n"),
        SourceChunk("src/code.py", 4, 7, "4\n5\n6\n7\n"),
        SourceChunk("src/code.py", 7, 9, "7\n8\n9"),
    ]


@pytest.mark.parametrize(
    ("length", "window", "overlap", "expected"),
    [
        (0, 4, 1, []),
        (3, 4, 1, [(1, 3)]),
        (4, 4, 1, [(1, 4)]),
        (5, 4, 1, [(1, 4), (4, 5)]),
        (7, 4, 1, [(1, 4), (4, 7)]),
        (3, 1, 0, [(1, 1), (2, 2), (3, 3)]),
        (9, 4, 0, [(1, 4), (5, 8), (9, 9)]),
        (6, 4, 3, [(1, 4), (2, 5), (3, 6)]),
    ],
)
def test_window_edges(
    length: int, window: int, overlap: int, expected: list[tuple[int, int]]
) -> None:
    chunks = chunk_text(
        "code", "x\n" * length, kind="text", window_lines=window, overlap_lines=overlap
    )
    assert ranges(chunks) == expected


@pytest.mark.parametrize("kind", ["text", "markdown"])
@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["\n"],
        ["\n", "\n"],
        ["a\n", "\n"],
        ["a\r\n", "\r\n", "b"],
        ["a\n", "b\r\n", "c\n"],
        ["\ufeffUnicode \u4e2d\u6587 \U0001f600\n", "\t  fin "],
        ["a\rb\vc\fd\x85e\u2028f\u2029g"],
        ["a\r"],
    ],
)
def test_physical_lines_are_preserved(
    kind: Literal["text", "markdown"], lines: list[str]
) -> None:
    path = "../opaque\\path/\u4e2d.md"
    chunks = chunk_text(
        path, "".join(lines), kind=kind, window_lines=1, overlap_lines=0
    )
    assert chunks == [
        SourceChunk(path, index, index, line) for index, line in enumerate(lines, 1)
    ]


@pytest.mark.parametrize("text", ["", "some text"])
@pytest.mark.parametrize(
    ("window", "overlap", "parameter"),
    [
        (0, 0, "window_lines"),
        (-1, 0, "window_lines"),
        (True, 0, "window_lines"),
        (False, 0, "window_lines"),
        (2.0, 0, "window_lines"),
        ("2", 0, "window_lines"),
        (None, 0, "window_lines"),
        (2, -1, "overlap_lines"),
        (2, 2, "overlap_lines"),
        (2, 3, "overlap_lines"),
        (2, True, "overlap_lines"),
        (2, False, "overlap_lines"),
        (2, 0.0, "overlap_lines"),
        (2, "0", "overlap_lines"),
        (2, None, "overlap_lines"),
    ],
)
def test_invalid_parameters_fail_even_for_empty_input(
    text: str, window: int, overlap: int, parameter: str
) -> None:
    with pytest.raises(ValueError, match=parameter):
        chunk_text(
            "file", text, kind="text", window_lines=window, overlap_lines=overlap
        )


@pytest.mark.parametrize("text", ["", "content"])
@pytest.mark.parametrize("kind", ["", "Markdown", "code", None, True])
def test_invalid_kind(text: str, kind: Literal["text", "markdown"]) -> None:
    with pytest.raises(ValueError, match="kind"):
        chunk_text("file", text, kind=kind)


def test_markdown_sections_and_overflow() -> None:
    assert chunk_text(
        "doc", "# A\na\n## B\nb\n", kind="markdown", window_lines=4, overlap_lines=1
    ) == [SourceChunk("doc", 1, 2, "# A\na\n"), SourceChunk("doc", 3, 4, "## B\nb\n")]
    assert chunk_text(
        "doc",
        "intro\n# A\na\nb\nc\nd\n# B\nz\n",
        kind="markdown",
        window_lines=3,
        overlap_lines=1,
    ) == [
        SourceChunk("doc", 1, 1, "intro\n"),
        SourceChunk("doc", 2, 4, "# A\na\nb\n"),
        SourceChunk("doc", 4, 6, "b\nc\nd\n"),
        SourceChunk("doc", 7, 8, "# B\nz\n"),
    ]


@pytest.mark.parametrize("level", range(1, 7))
@pytest.mark.parametrize("indent", range(4))
@pytest.mark.parametrize("suffix", ["", " Title", "\tTitle", " Title ###"])
def test_supported_atx_headings(level: int, indent: int, suffix: str) -> None:
    heading = " " * indent + "#" * level + suffix
    chunks = chunk_text(
        "doc", "intro\n" + heading, kind="markdown", window_lines=4, overlap_lines=1
    )
    assert chunks == [
        SourceChunk("doc", 1, 1, "intro\n"),
        SourceChunk("doc", 2, 2, heading),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "    # indented",
        "\t# tab",
        "####### seven",
        "#word",
        "\\# escaped",
        "> # quote",
        "- # list",
        "Title\n===",
        "Title\n---",
        "<h1>HTML</h1>",
        "#\r",
        "#\v",
        "#\u00a0nonbreaking",
        "\ufeff# BOM",
    ],
)
def test_unsupported_heading_forms_are_literal(body: str) -> None:
    text = "intro\n" + body
    chunks = chunk_text("doc", text, kind="markdown", window_lines=8, overlap_lines=1)
    assert chunks == [SourceChunk("doc", 1, text.count("\n") + 1, text)]


def test_blank_preamble_adjacent_headings_and_preceding_blank_lines() -> None:
    assert chunk_text(
        "doc",
        "\n# A\n## B\nb\n\n# C\n",
        kind="markdown",
        window_lines=8,
        overlap_lines=1,
    ) == [
        SourceChunk("doc", 1, 1, "\n"),
        SourceChunk("doc", 2, 2, "# A\n"),
        SourceChunk("doc", 3, 5, "## B\nb\n\n"),
        SourceChunk("doc", 6, 6, "# C\n"),
    ]


def test_html_has_no_special_block_state() -> None:
    text = "<div>\n# heading\n</div>\n"
    assert ranges(chunk_text("doc", text, kind="markdown")) == [(1, 1), (2, 3)]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    ("opener", "false_closer", "closer"),
    [
        ("```python", "~~~", "```"),
        ("~~~info `allowed`", "```", "~~~"),
        ("````", "```", "`````"),
        ("~~~~", "~~~", "~~~~~"),
        (" ```", "``` text", "   ```\t "),
        ("  ~~~", "~~~ text", " ~~~~"),
        ("   ```", "    ```", "```"),
        ("```", "\t```", "```"),
        ("```", "```\r text", "```"),
    ],
)
def test_fences_suppress_headings_until_valid_close(
    newline: str, opener: str, false_closer: str, closer: str
) -> None:
    lines = [
        "# A",
        opener,
        "# code",
        false_closer,
        "## still code",
        closer,
        "# B",
        "end",
    ]
    text = newline.join(lines) + newline
    chunks = chunk_text("doc", text, kind="markdown", window_lines=10, overlap_lines=1)
    assert chunks == [
        SourceChunk("doc", 1, 6, newline.join(lines[:6]) + newline),
        SourceChunk("doc", 7, 8, newline.join(lines[6:]) + newline),
    ]


@pytest.mark.parametrize("opener", ["```bad`info", "    ```", "\t~~~", "``", "~~"])
def test_invalid_fence_openers_do_not_hide_headings(opener: str) -> None:
    text = opener + "\n# heading\n"
    assert ranges(chunk_text("doc", text, kind="markdown")) == [(1, 1), (2, 2)]


@pytest.mark.parametrize("opener", ["```", "~~~"])
def test_unclosed_fence_hides_headings_through_eof(opener: str) -> None:
    text = "preamble\n" + opener + "\n# code\n## more code"
    assert chunk_text("doc", text, kind="markdown") == [SourceChunk("doc", 1, 4, text)]


def test_fence_state_survives_window_boundaries() -> None:
    text = "# A\n```\nx\n# code\n```\n# B\ny\n"
    assert chunk_text(
        "doc", text, kind="markdown", window_lines=3, overlap_lines=1
    ) == [
        SourceChunk("doc", 1, 3, "# A\n```\nx\n"),
        SourceChunk("doc", 3, 5, "x\n# code\n```\n"),
        SourceChunk("doc", 6, 7, "# B\ny\n"),
    ]


def assert_preserved(
    lines: list[str], kind: Literal["text", "markdown"], window: int, overlap: int
) -> list[SourceChunk]:
    text = "".join(lines)
    path = "opaque/\u4e2d\\file"
    chunks = chunk_text(
        path, text, kind=kind, window_lines=window, overlap_lines=overlap
    )
    assert chunks == chunk_text(
        path, text, kind=kind, window_lines=window, overlap_lines=overlap
    )
    previous_start = covered_end = 0
    reconstructed: list[str] = []
    for chunk in chunks:
        assert chunk.path == path
        assert 1 <= chunk.start_line <= chunk.end_line <= len(lines)
        assert chunk.end_line - chunk.start_line + 1 <= window
        assert chunk.content == "".join(lines[chunk.start_line - 1 : chunk.end_line])
        assert previous_start < chunk.start_line <= covered_end + 1
        assert covered_end < chunk.end_line
        if overlap == 0:
            assert chunk.start_line == covered_end + 1
        reconstructed.extend(lines[covered_end : chunk.end_line])
        previous_start, covered_end = chunk.start_line, chunk.end_line
    assert covered_end == len(lines)
    assert "".join(reconstructed) == text
    return chunks


@pytest.mark.parametrize("kind", ["text", "markdown"])
@pytest.mark.parametrize("seed", range(200))
def test_generated_source_preservation(
    seed: int, kind: Literal["text", "markdown"]
) -> None:
    rng = random.Random(seed)
    alphabet = "ab #`~\t\r\v\f\x85\u2028\u2029\ufeff\u4e2d\U0001f600"
    special = ["", "# A", "## B", "```", "~~~", "````python", "   #", "#word"]
    lines = []
    for _ in range(rng.randrange(61)):
        body = (
            rng.choice(special)
            if rng.randrange(2)
            else "".join(rng.choice(alphabet) for _ in range(rng.randrange(25)))
        )
        lines.append(body + rng.choice(["\n", "\r\n"]))
    if lines and rng.randrange(2):
        lines[-1] = "final " + rng.choice(special)
    window = rng.randrange(1, 26)
    assert_preserved(lines, kind, window, rng.randrange(window))


@pytest.mark.parametrize("seed", range(200))
def test_generated_markdown_sections_never_overlap(seed: int) -> None:
    rng = random.Random(seed)
    sections: list[tuple[int, int]] = []
    lines = ["preamble\n"] * rng.randrange(4)
    if lines:
        sections.append((1, len(lines)))
    for _ in range(rng.randrange(1, 8)):
        start = len(lines) + 1
        lines.append("#" * rng.randrange(1, 7) + " heading\r\n")
        lines.extend(["body\n"] * rng.randrange(15))
        sections.append((start, len(lines)))
    window = rng.randrange(1, 26)
    chunks = assert_preserved(lines, "markdown", window, rng.randrange(window))
    for chunk in chunks:
        assert any(
            start <= chunk.start_line <= chunk.end_line <= end
            for start, end in sections
        )
    assert {start for start, _ in sections} <= {chunk.start_line for chunk in chunks}
