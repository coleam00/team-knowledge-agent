from pathlib import Path, PurePosixPath

from .contracts import CloneLimits, CloneSelectError, SelectedFile
from .git import GitRunner, object_id

MAX_FILE_BYTES = 1_000_000
_DIRECTORIES = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".venv",
    "__pycache__",
}
_LOCKFILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "bun.lockb",
    "go.sum",
    "packages.lock.json",
    "composer.lock",
}
_LANGUAGES = {
    extension: language
    for language, extensions in {
        "markdown": (".md", ".markdown"),
        "rst": (".rst",),
        "text": (".txt",),
        "python": (".py", ".pyi"),
        "javascript": (".js", ".jsx", ".mjs", ".cjs"),
        "typescript": (".ts", ".tsx"),
        "go": (".go",),
        "java": (".java",),
        "c": (".c", ".h"),
        "cpp": (".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
        "rust": (".rs",),
        "ruby": (".rb",),
        "shell": (".sh", ".bash", ".zsh"),
        "sql": (".sql",),
        "css": (".css",),
        "html": (".html", ".htm"),
    }.items()
    for extension in extensions
}
_BINARY_CONTROLS = bytes([*range(9), 11, *range(14, 32), 127])


def path_language(path: str) -> str | None:
    if (
        not path
        or "\\" in path
        or (
            len(path) >= 2
            and path[0].isascii()
            and path[0].isalpha()
            and path[1] == ":"
        )
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        return None
    parts = path.lower().split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if any(part in _DIRECTORIES for part in parts[:-1]):
        return None
    name = parts[-1]
    if name in _LOCKFILES or name.endswith(
        (".lock", ".min.js", ".min.mjs", ".min.cjs", ".min.css")
    ):
        return None
    return "text" if name == "readme" else _LANGUAGES.get(PurePosixPath(name).suffix)


def select_files(
    runner: GitRunner,
    repository: Path,
    commit: str,
    limits: CloneLimits,
) -> tuple[SelectedFile, ...]:
    tree = runner.run("ls-tree", "-r", "-z", "-l", commit, git_dir=repository).stdout
    selected: list[SelectedFile] = []
    total_bytes = 0
    for record in tree.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, blob, raw_size = metadata.split()
        except ValueError:
            raise CloneSelectError("git_failed") from None
        if mode not in (b"100644", b"100755") or kind != b"blob":
            continue
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            continue
        language = path_language(path)
        if language is None:
            continue
        try:
            size = int(raw_size)
        except ValueError:
            raise CloneSelectError("git_failed") from None
        if size < 0:
            raise CloneSelectError("git_failed")
        if size > MAX_FILE_BYTES:
            continue
        blob_sha = object_id(blob)
        data = runner.run("cat-file", "blob", blob_sha, git_dir=repository).stdout
        if len(data) != size:
            raise CloneSelectError("git_failed")
        if any(char in data for char in _BINARY_CONTROLS):
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(selected) == limits.max_files:
            raise CloneSelectError("file_count_limit")
        total_bytes += size
        if total_bytes > limits.max_total_bytes:
            raise CloneSelectError("total_bytes_limit")
        selected.append(SelectedFile(path, blob_sha, language, size, content))
    return tuple(sorted(selected, key=lambda file: file.path))
