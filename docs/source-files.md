# Indexed source files

`GET /api/sources/{id}/file?path=docs/deploy.md&start=14&end=18`
returns eligible text from the source's persisted `indexed_commit`. It never
fetches upstream, reads working files, or resolves HEAD. Registration still
leaves sources queued: until the worker exists, tests and runtime checks use
`clone_and_select` and update the source record directly. No `files` rows are
required by this reader.

HTTP and future adapters share the synchronous service:

```python
from app.source_files import fetch_source_file

result = fetch_source_file(source_id, path="docs/deploy.md", start=14, end=18,
                           settings=settings)
```

The frozen `SourceFile` dataclass has exactly these JSON fields:

| Field | Meaning |
|---|---|
| `source_id` | Source UUID as a string |
| `path` | Exact case-sensitive Git path |
| `commit` | Full persisted commit ID captured for this request |
| `start_line`, `end_line` | Actual 1-based inclusive bounds, both null for an empty result |
| `total_lines` | Line count of the complete file |
| `content` | Exact selected UTF-8 text, with original line endings |

Only LF separates lines, as in the chunking library. CRLF, BOMs, lone CR,
form feeds and Unicode separators are preserved. A final LF ends a line; it
does not add another one. Omit both bounds for the whole file; start-only reads
through EOF, and end-only starts at line 1. End clips to EOF. A start beyond EOF
and an empty file return empty content with null bounds. Explicit reversed,
zero, negative and noninteger bounds are invalid; direct service calls also
reject boolean bounds.

The source identity and commit are captured in one database query. Status does
not gate access to a previous index. An in-flight request may finish at its
captured commit while the registry advances; subsequent requests see the new
commit. Old deleted or renamed paths then return 404, even if older objects
remain cached.

## Eligibility and confinement

The reader uses the same [file selection policy](file-selection.md) as ingestion:
regular blobs (`100644` or `100755`), supported paths, at most 1,000,000 bytes,
strict UTF-8 and no disallowed binary controls. Symlinks, gitlinks, directories,
binary files, excluded paths and oversized files are unavailable. Empty eligible
files are supported. Whole-repository acquisition limits do not limit a read.

Paths must be nonempty relative Git paths. Absolute paths, Windows drive
prefixes, backslashes, ASCII controls/DEL, empty components, `.` and `..`
components, and unencodable Unicode are rejected, never normalized. Unicode,
spaces, punctuation and colons outside a drive prefix are preserved. HTTP
decodes query strings once; the service does not decode again. Tree paths are
compared exactly in Python, so glob-like names never become patterns.

Reads open an existing UUID-owned bare cache after matching its source marker,
checking confinement and rejecting linked/external object stores, included
config, executable alternate-ref commands and promisor/partial-clone config.
The reader's Git commands disable transports and lazy fetching, use isolated
environment/config/hooks, and have a 30-second deadline per command. Only full
lowercase SHA-1 or SHA-256 object IDs are accepted. The captured object must be
a commit; matching regular blobs are read by object ID and checked for size.
An unrelated missing blob does not block an intact file. Tree framing and the
requested blob's metadata and content must still be readable and valid.

Reads and acquisition share the exclusive source lock. A read writes only its
owned transient lock/execution directory, which is cleaned on success or failure;
objects, refs, config and markers remain unchanged. Missing caches are never
created. Local sources must be enabled, but the original local repository need
not still exist. The data root remains trusted: these checks do not provide
protection against a same-user process changing storage concurrently. Tree
metadata is scanned in full; there is no new total-tree resource cap.

## Errors

The service raises `SourceFileError` with a typed `code`. HTTP uses fixed messages
that do not expose locations, SQL errors, Git stderr or file contents.

| Code | HTTP | Meaning |
|---|---|---|
| `not_found` | 404 | Missing source, indexed commit, path (including omitted/empty), cache, commit object, or eligible file |
| `invalid_path` | 422 | Unsafe path syntax |
| `invalid_range` | 422 | Invalid line bounds |
| `unavailable` | 503 | Database/Git failure, timeout, malformed object data, invalid commit ID/type, disabled local sources, or unsafe/mismatched storage |
| `busy` | 503 | Source lock held; `Retry-After: 1` |

Transport parsing also returns sanitized 422 responses for invalid UUIDs or
noninteger query bounds. Missing cache and file responses do not fetch or retry.
