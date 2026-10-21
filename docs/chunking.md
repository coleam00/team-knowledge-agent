# Exact source chunks

`app.chunking.chunk_text` is a pure library for decoded source text. It performs
no file, database, network, or model calls and never executes source content.
Indexing and persistence are separate, later increments.

```python
from app.chunking import chunk_text

chunks = chunk_text(
    "src/example.py",
    "1\n2\n3\n4\n5\n6\n7\n8\n9",
    kind="text",
    window_lines=4,
    overlap_lines=1,
)
assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
    (1, 4), (4, 7), (7, 9)
]
```

The signature is `chunk_text(path: str, text: str, *,
kind: Literal["text", "markdown"], window_lines: int = 80,
overlap_lines: int = 10) -> list[SourceChunk]`. The required `kind` selects line
windows (`"text"`, including code) or Markdown sections. It is not inferred from
the path. Each frozen `SourceChunk` contains `path: str`, `start_line: int`,
`end_line: int`, and `content: str`. Path is unchanged, opaque metadata; source
identity, commit, decoding, and file classification belong to the caller.

Unknown kinds raise `ValueError` naming `kind`. Window size must be a positive
integer, and overlap must be an integer with `0 <= overlap_lines < window_lines`.
Booleans and non-integer values are rejected with `ValueError` naming the invalid
parameter. Validation applies even to empty text, which otherwise returns `[]`.

## Physical lines and windows

Only LF (`\n`) separates physical lines, and each delimiter remains attached to
the preceding line. CRLF remains exactly `\r\n`; mixed endings are preserved.
A nonempty final suffix is an unterminated line. A terminal LF adds no phantom
line: `"\n"` is one blank line and `"a\n\n"` is two lines. Lone CR, vertical tab,
form feed, NEL, and Unicode line/paragraph separators are ordinary line content.
BOMs, blank lines, indentation, Unicode, and trailing spaces are retained.

For this preserved line sequence, each chunk obeys:

```python
1 <= chunk.start_line <= chunk.end_line <= len(lines)
chunk.content == "".join(lines[chunk.start_line - 1 : chunk.end_line])
chunk.end_line - chunk.start_line + 1 <= window_lines
```

No separator is inserted when joining. Future file readers must preserve decoded
newlines, for example with `open(..., encoding="utf-8", newline="")`; default
universal-newline reading can silently change CRLF before the library sees it.
The guarantee applies to the input string, not its original encoding or bytes.

Windows advance by `window_lines - overlap_lines` and stop as soon as a window
reaches the interval's end. Exactly four lines with window 4/overlap 1 yield only
`[1,4]`, without a redundant tail. Output is in source order with no missing
lines. Line limits do not bound characters or model tokens: a long line remains
whole. Overlap near the window size can produce substantial duplication.

## Markdown boundaries

Markdown uses a deliberately limited lexical grammar, not a full CommonMark
parser:

- ATX headings have zero to three literal leading spaces, one to six `#`, then
  space, tab, or end of line. Empty headings and optional closing hashes work;
  all levels create boundaries in the same way.
- Four-space/tab indentation, seven hashes, `#word`, escaped hashes, and headings
  prefixed by blockquote/list markers do not create boundaries.
- An opening fence has zero to three literal leading spaces and at least three
  backticks or tildes. The maximal marker run determines its length. An info
  string is allowed; a backtick opener's remainder must contain no backticks.
  Tilde openers permit any remainder.
- Within a fence, headings are literal. A closing fence has zero to three literal
  leading spaces, the same marker character at least as many times as the opener,
  then only spaces/tabs. Shorter, wrong-character, or text-suffixed markers do not
  close it. An unclosed fence suppresses headings through EOF. A closing line
  cannot also reopen the fence.
- Recognition ignores only a terminal LF and its immediately preceding CR;
  stored content is never stripped. Setext underlines, HTML headings, and nested
  container syntax are unsupported boundary forms and remain literal content.
  There is no HTML/container block state: a bare ATX-looking line outside a
  recognized fence creates a boundary even within an HTML construct.

A heading belongs to its new section. Any preceding blank lines stay with the
previous section. Nonempty preambles, including blank-only preambles, form their
own section. A heading at line 1 creates no empty preamble; adjacent headings can
create one-line sections. Short sections are never merged.

For `"# A\na\n## B\nb\n"`, window 4/overlap 1 gives `[1,2]` and `[3,4]`.
For `"intro\n# A\na\nb\nc\nd\n# B\nz\n"`, window 3/overlap 1 gives
`[1,1]`, `[2,4]`, `[4,6]`, `[7,8]`. The shared window rule applies independently
within each section: overlap never crosses a heading boundary. Headings are not
synthetically repeated in continuation chunks. Fence state is scanned across the
whole file before windowing. A window can split a fenced block or lack a heading
or opening fence; exact contiguous source ranges take precedence over rendering
each snippet independently.

## Approved defaults (MISSION Q1)

The default window of **80 lines** and overlap of **10 lines**, for both modes,
were proposed under MISSION Q1 and approved by the owner on 2026-10-21 in
[issue #8](https://github.com/coleam00/team-knowledge-agent/issues/8#issuecomment-6312041512).
The decision is recorded in MISSION.md. Markdown still splits on headings first.
These defaults bound line count with 12.5% overlap; retrieval-quality tuning is
deferred until real answer-quality evidence is available. Both parameters remain
caller-controlled, and correctness tests cover explicit sizes independently of
these defaults.
