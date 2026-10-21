# Repository acquisition and file selection

`app.ingest.clone_and_select(source, *, settings, limits)` is a synchronous
library primitive. It returns committed repository text for future ingestion;
registration, jobs, database persistence, chunking and indexing are not built by
this API.

Pass `SourceSpec(source_id: UUID, location: str)`, `Settings`, and
`CloneLimits(max_files, max_total_bytes, subprocess_timeout_seconds)`. All limits
are required: positive integer counts/bytes and a positive finite timeout, with
booleans rejected. These are caller-supplied policies, **not approved production
cap defaults** (MISSION Q3 remains open).

The frozen `SelectionResult` contains the source UUID, canonical `repo_url`, one
resolved `commit_sha`, and a tuple of `SelectedFile` values sorted by exact path.
Each file contains its Git-relative POSIX `path`, full `blob_sha`, `language`, raw
`size_bytes` and decoded `content`. Content and input locations are omitted from
repr. An empty selection succeeds; an unborn repository fails. No partial result
is returned when an operation or limit fails.

## Accepted sources

Public sources are HTTPS GitHub repository URLs with exactly an owner and repo,
optionally ending in `.git` and/or one slash. Host matching is case-insensitive;
canonical output is lowercase `https://github.com/owner/repo`. The owner contains
ASCII letters, digits and hyphens with alphanumeric endpoints; the repo contains
ASCII letters, digits, dots, underscores and hyphens, excluding `.` and `..`.
Credentials, ports, query strings, fragments, percent escapes, whitespace,
backslashes, other hosts/protocols and branch/file URLs are rejected before Git.
Access is anonymous. Redirects are disabled; moved repos need their current URL.

`Settings.allow_local_sources` defaults to false. `Settings.from_env()` enables
it only for `TKA_ALLOW_LOCAL_SOURCES=1`. Enabled local sources must be absolute
paths to normal non-bare repositories with an actual `.git` directory. Linked
worktrees, common-directory redirection (`.git/commondir`), symlinks/junctions
anywhere in Git metadata, object alternate files,
config includes and `core.alternateRefsCommand` are rejected. The config must
have exactly one public GitHub `remote.origin.url`, used only for citation
metadata. Local acquisition never contacts that URL. Only committed HEAD is
read; uncommitted working files are irrelevant. The storage root cannot be inside
the source repository, and acquisition never writes to the input repository.
Local SHA-1 and SHA-256 repositories are supported; bare storage is initialized
with the validated source's object format. Public GitHub storage uses SHA-1.

## Non-execution boundary

Git 2.47 or newer is required. An absolute installed Git executable runs with an
allowlist of OS launch environment variables, isolated empty home/config paths,
no inherited Git or provider settings, no credential helpers or interactive
prompts, hooks redirected to an empty directory, and fsmonitor, automatic
maintenance and recursive submodules disabled. Protocols default to denied;
HTTPS is allowed, and file transport is enabled only for opted-in local sources.
TLS verification stays on. Git's trusted transport helpers can run; repository
programs cannot. Keep the installed Git patched: this is not a sandbox against
vulnerabilities in Git itself or another process with the same filesystem access.

Acquisition uses bare init with no template and one fetch operation:
`git fetch --force --no-tags --no-recurse-submodules <location> +HEAD:refs/tka/snapshot`.
Every fetch follows the source's current HEAD, including default-branch changes.
Selection resolves that ref once to a full commit ID, reads its NUL-delimited
`ls-tree` records, then reads raw blob IDs with `cat-file blob`. There is no
checkout, filesystem content walker, filter/textconv application, LFS download,
submodule update or source/build execution. All symlinks and gitlinks are skipped.
Executable-mode regular files remain eligible as text.

Every command owns its descendants: a process group on Unix or kill-on-close
Job Object on Windows. A trusted Python worker waits for ownership before
launching Git. Output uses temporary files rather than descendant-held pipes.
Timeouts terminate and reap the owned command and terminate its descendants;
cleanup is also performed on success and exceptions. Cleanup has its own bounded
wait, so a 0.01-second command timeout is not a 10 ms wall-clock guarantee.

The process lifecycle belongs to `app/ingest/`; application snapshots need only
the application package. Protected factory infrastructure remains independent.

## File-record schema

Startup migration `0002_files.sql` creates `files(id, source_id, path, blob_sha,
language)` in the configured Postgres database. IDs are UUIDs, all columns are
required, and `(source_id, path)` is unique. The same path can belong to different
sources, and blob IDs preserve either supported Git hash format as text.
Source IDs are caller-owned, matching `SourceSpec`; there is no `sources` table
yet, so this migration does not add a foreign key to a nonexistent registry.
Acquisition returns metadata without writing rows. Worker persistence and source
registration remain later increments; this schema supplies their file-record seam.

## Exact matching rules

Rules match case-insensitively but preserve original path spelling. Paths must
be UTF-8, relative POSIX paths without empty, `.` or `..` components, backslashes,
ASCII controls (including DEL), or a drive prefix such as `C:`. Tree records
accept only regular blob modes `100644` and `100755`.

Exclusions run before inclusions:

1. Any directory component exactly `.git`, `node_modules`, `vendor`, `dist`,
   `build`, `.venv` or `__pycache__` is skipped. `builder/guide.md` is eligible.
2. Basenames ending in `.lock`, or exactly `package-lock.json`,
   `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `bun.lockb`, `go.sum`,
   `packages.lock.json` or `composer.lock` are skipped.
3. Basenames ending in `.min.js`, `.min.mjs`, `.min.cjs` or `.min.css` are skipped.
   No entropy or long-line heuristic is used.
4. Bare `README` is `text`, at any depth. All other names, including READMEs
   with extensions, require the final extension below. Everything else is skipped.

| Extensions | Language |
| --- | --- |
| `.md`, `.markdown` | `markdown` |
| `.rst` | `rst` |
| `.txt` | `text` |
| `.py`, `.pyi` | `python` |
| `.js`, `.jsx`, `.mjs`, `.cjs` | `javascript` |
| `.ts`, `.tsx` | `typescript` |
| `.go` | `go` |
| `.java` | `java` |
| `.c`, `.h` | `c` |
| `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` | `cpp` |
| `.rs` | `rust` |
| `.rb` | `ruby` |
| `.sh`, `.bash`, `.zsh` | `shell` |
| `.sql` | `sql` |
| `.css` | `css` |
| `.html`, `.htm` | `html` |

**1 MB = 1,000,000 bytes.** Supported text at exactly that size is eligible;
1,000,001 bytes is skipped before reading content. Text must decode as strict
UTF-8 and contain no ASCII controls except TAB, LF, FF and CR: reject byte ranges
0x00–0x08, 0x0B, 0x0E–0x1F and 0x7F. Empty files qualify. BOM, CRLF, trailing
newlines and every other character are preserved exactly. Blob size must match
tree metadata; a missing/corrupt blob fails the operation.

Only eligible text consumes file and byte limits. Equality succeeds; exceeding
either limit fails the whole call. These limits do **not** cap downloaded packs,
object storage, total tree entries, total operation duration or disk usage. Those
deployment policies must be settled before worker deployment.

## Cache and errors

Storage is `<data_dir>/repos/<source_id.hex>/repository.git`, with sibling
`source.json` binding UUID, location, kind and citation URL. IDs must be UUIDs;
source strings never become directory components. Application-owned paths reject
symlinks/junctions and must resolve beneath the configured trusted data root.
The initial store is published by rename only after a successful full selection.
Failure cleans up only that call's temporary store. Reuse preserves old commit
objects; failed selection on reuse may leave newly fetched objects/ref state but
does not claim an indexed commit. A changed identity is refused, never replaced.

An exclusive sibling `<source_id.hex>.lock` directory serializes each source.
Crashes may leave locks behind. Remove a stale lock manually only after confirming
that its owner is no longer running; there is no automatic lock stealing.

`CloneSelectError.code` is stable; messages and tracebacks never expose raw Git
stderr, supplied locations or captured exceptions:

| Codes | Meaning |
| --- | --- |
| `invalid_source`, `local_sources_disabled`, `invalid_local_repository` | Rejected input or unsupported local layout/config |
| `source_mismatch`, `invalid_storage`, `busy` | Cache identity, filesystem safety, or exclusive lock failure |
| `not_found` | Missing/unavailable repository, including anonymous authentication refusal |
| `network` | Recognized transport/DNS/connection/TLS failure |
| `empty_repository` | No committed HEAD |
| `git_unavailable`, `git_failed`, `timeout` | Missing/old Git, unclassified Git failure, or command deadline |
| `file_count_limit`, `total_bytes_limit` | Caller-supplied selected-content limit exceeded |
