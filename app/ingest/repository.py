import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import Settings

from .contracts import (
    CloneLimits,
    CloneSelectError,
    SelectedFile,
    SelectionResult,
    SourceSpec,
)
from .git import GitRunner, object_id
from .selection import eligible_record, read_blob, select_files, valid_path

_PUBLIC_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"([A-Za-z0-9._-]+)/?",
    re.IGNORECASE | re.ASCII,
)


def canonical_url(location: str) -> str:
    match = _PUBLIC_URL.fullmatch(location)
    if match is None:
        raise CloneSelectError("invalid_source")
    owner, name = (part.lower() for part in match.groups())
    if name.endswith(".git"):
        name = name[:-4]
    if name in ("", ".", ".."):
        raise CloneSelectError("invalid_source")
    return f"https://github.com/{owner}/{name}"


def _safe_path(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise CloneSelectError("invalid_storage")
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise CloneSelectError("invalid_storage")
        current = current.parent
    if not path.resolve().is_relative_to(root):
        raise CloneSelectError("invalid_storage")


def _safe_tree(root: Path, directory: Path) -> None:
    _safe_path(root, directory)
    for parent, directories, files in directory.walk(follow_symlinks=False):
        for name in (*directories, *files):
            _safe_path(root, parent / name)


def _remove_owned(root: Path, directory: Path) -> None:
    _safe_tree(root, directory)

    def remove_readonly(
        operation: Callable[..., object],
        name: str,
        error: BaseException,
    ) -> None:
        path = Path(name)
        _safe_path(directory, path)
        if (
            os.name != "nt"
            or not isinstance(error, PermissionError)
            or not path.is_file()
        ):
            raise error
        # Git objects are read-only on Windows, even in this call's disposable store.
        path.chmod(stat.S_IWRITE)
        operation(name)

    shutil.rmtree(directory, onexc=remove_readonly)


def _local_path(location: str, settings: Settings) -> Path:
    if not settings.allow_local_sources:
        raise CloneSelectError("local_sources_disabled")
    path = Path(location).resolve()
    if not path.exists():
        raise CloneSelectError("not_found")
    git_dir = path / ".git"
    if (
        not git_dir.is_dir()
        or git_dir.is_symlink()
        or git_dir.is_junction()
        or (git_dir / "commondir").exists()
        or (git_dir / "objects/info/alternates").exists()
        or (git_dir / "objects/info/http-alternates").exists()
    ):
        raise CloneSelectError("invalid_local_repository")
    try:
        _safe_tree(path, git_dir)
    except CloneSelectError:
        raise CloneSelectError("invalid_local_repository") from None
    return path


def _local_origin(path: Path, runner: GitRunner) -> str:
    arguments = (
        "config",
        "--no-includes",
        "--null",
        "--file",
        str(path / ".git/config"),
    )
    keys = runner.run(*arguments, "--name-only", "--list").stdout.lower().split(b"\0")
    if any(
        key == b"include.path"
        or key == b"core.alternaterefscommand"
        or (key.startswith(b"includeif.") and key.endswith(b".path"))
        for key in keys
    ):
        raise CloneSelectError("invalid_local_repository")
    origins = runner.run(
        *arguments, "--get-all", "remote.origin.url", allowed_codes=(0, 1)
    )
    values = origins.stdout.split(b"\0")
    if len(values) != 2 or values[-1] != b"":
        raise CloneSelectError("invalid_local_repository")
    try:
        return canonical_url(values[0].decode("utf-8"))
    except (UnicodeDecodeError, CloneSelectError):
        raise CloneSelectError("invalid_local_repository") from None


def _local_object_format(path: Path, runner: GitRunner) -> str:
    value = runner.run(
        "rev-parse", "--show-object-format", git_dir=path / ".git"
    ).stdout.strip()
    if value not in (b"sha1", b"sha256"):
        raise CloneSelectError("invalid_local_repository")
    return value.decode("ascii")


@contextmanager
def _source_lock(root: Path, source: SourceSpec) -> Iterator[Path]:
    lock = root / "repos" / f"{source.source_id.hex}.lock"
    _safe_path(root, lock)
    try:
        lock.mkdir()
    except FileExistsError:
        raise CloneSelectError("busy") from None
    try:
        yield lock
    finally:
        _remove_owned(root, lock)


def _validate_cache(root: Path, destination: Path, marker: dict[str, str]) -> Path:
    _safe_tree(root, destination)
    try:
        stored = json.loads((destination / "source.json").read_text("utf-8"))
    except (ValueError, OSError):
        raise CloneSelectError("invalid_storage") from None
    if isinstance(stored, dict) and stored.get("kind") == "local":
        location = stored.get("location")
        if not isinstance(location, str):
            raise CloneSelectError("invalid_storage")
        stored["location"] = os.path.normcase(os.path.normpath(location))
        marker = {
            **marker,
            "location": os.path.normcase(os.path.normpath(marker["location"])),
        }
    if stored != marker:
        raise CloneSelectError("source_mismatch")
    repository = destination / "repository.git"
    if not repository.is_dir() or any(
        (repository / path).exists()
        for path in (
            "commondir",
            "objects/info/alternates",
            "objects/info/http-alternates",
        )
    ):
        raise CloneSelectError("invalid_storage")
    return repository


def _validate_cache_config(repository: Path, runner: GitRunner) -> None:
    keys = (
        runner.run(
            "config",
            "--no-includes",
            "--null",
            "--file",
            str(repository / "config"),
            "--name-only",
            "--list",
        )
        .stdout.lower()
        .split(b"\0")
    )
    if any(
        key.startswith((b"include.", b"includeif."))
        or key
        in (
            b"core.alternaterefscommand",
            b"core.worktree",
            b"extensions.partialclone",
            b"extensions.worktreeconfig",
        )
        or (
            key.startswith(b"remote.")
            and key.endswith((b".promisor", b".partialclonefilter"))
        )
        for key in keys
    ):
        raise CloneSelectError("invalid_storage")
    bare = runner.run(
        "config",
        "--no-includes",
        "--file",
        str(repository / "config"),
        "--type=bool",
        "--get",
        "core.bare",
        allowed_codes=(0, 1),
    )
    if bare.stdout != b"true\n":
        raise CloneSelectError("invalid_storage")


def _acquire(
    source: SourceSpec,
    settings: Settings,
    limits: CloneLimits,
) -> SelectionResult:
    local = Path(source.location).is_absolute()
    path = _local_path(source.location, settings) if local else None
    url = canonical_url(source.location) if path is None else None
    root = settings.data_dir.resolve()
    if path is not None and root.is_relative_to(path):
        raise CloneSelectError("invalid_storage")
    root.mkdir(parents=True, exist_ok=True)
    repositories = root / "repos"
    _safe_path(root, repositories)
    repositories.mkdir(exist_ok=True)
    destination = repositories / source.source_id.hex
    temporary: Path | None = None
    with _source_lock(root, source) as lock:
        try:
            runner = GitRunner(lock, limits.subprocess_timeout_seconds, local=local)
            if path is not None:
                url = _local_origin(path, runner)
            assert url is not None
            location = str(path) if path is not None else url + ".git"
            marker = {
                "source_id": str(source.source_id),
                "location": location,
                "kind": "local" if local else "github",
                "repo_url": url,
            }
            _safe_tree(root, destination)
            if destination.exists():
                _validate_cache(root, destination, marker)
                storage = destination
            else:
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f"{source.source_id.hex}-", dir=repositories
                    )
                )
                storage = temporary
                object_format = (
                    "sha1" if path is None else _local_object_format(path, runner)
                )
                runner.run(
                    "init",
                    "--bare",
                    "--template=",
                    f"--object-format={object_format}",
                    str(storage / "repository.git"),
                )
            repository = storage / "repository.git"
            runner.run(
                "fetch",
                "--force",
                "--no-tags",
                "--no-recurse-submodules",
                location,
                "+HEAD:refs/tka/snapshot",
                git_dir=repository,
            )
            commit = object_id(
                runner.run(
                    "rev-parse",
                    "--verify",
                    "refs/tka/snapshot^{commit}",
                    git_dir=repository,
                ).stdout.strip()
            )
            files = select_files(runner, repository, commit, limits)
            result = SelectionResult(source.source_id, url, commit, files)
            if temporary is not None:
                _safe_tree(root, temporary)
                (temporary / "source.json").write_text(
                    json.dumps(marker), encoding="utf-8"
                )
                _safe_path(root, destination)
                temporary.rename(destination)
                temporary = None
            return result
        finally:
            if temporary is not None:
                _remove_owned(root, temporary)


def clone_and_select(
    source: SourceSpec,
    *,
    settings: Settings,
    limits: CloneLimits,
) -> SelectionResult:
    try:
        return _acquire(source, settings, limits)
    except CloneSelectError as error:
        raise CloneSelectError(error.code) from None
    except (OSError, ValueError):
        raise CloneSelectError("invalid_storage") from None


def read_cached_file(
    source: SourceSpec, *, repo_url: str, commit: str, path: str, settings: Settings
) -> SelectedFile:
    try:
        return _read_cached_file(source, repo_url, commit, path, settings)
    except CloneSelectError as error:
        raise CloneSelectError(error.code) from None
    except (OSError, ValueError):
        raise CloneSelectError("invalid_storage") from None


def _read_cached_file(
    source: SourceSpec, repo_url: str, commit: str, path: str, settings: Settings
) -> SelectedFile:
    commit = object_id(commit.encode("utf-8"))
    if not valid_path(path):
        raise CloneSelectError("invalid_source")
    local = Path(source.location).is_absolute()
    if local and not settings.allow_local_sources:
        raise CloneSelectError("local_sources_disabled")
    url = canonical_url(repo_url)
    location = source.location if local else canonical_url(source.location) + ".git"
    marker = {
        "source_id": str(source.source_id),
        "location": location,
        "kind": "local" if local else "github",
        "repo_url": url,
    }
    root = settings.data_dir.resolve()
    destination = root / "repos" / source.source_id.hex
    _safe_path(root, destination)
    if not destination.exists():
        raise CloneSelectError("not_found")
    with _source_lock(root, source) as lock:
        repository = _validate_cache(root, destination, marker)
        runner = GitRunner(lock, 30, local=False, offline=True)
        _validate_cache_config(repository, runner)
        probe = runner.run(
            "cat-file", "-t", commit, git_dir=repository, allowed_codes=(0, 128)
        )
        if probe.returncode == 128:
            if (
                probe.stderr.strip()
                == b"fatal: git cat-file: could not get object info"
            ):
                raise CloneSelectError("not_found")
            raise CloneSelectError("git_failed")
        if probe.stdout != b"commit\n":
            raise CloneSelectError("git_failed")
        tree = runner.run(
            "ls-tree", "-r", "-z", "-l", commit, git_dir=repository
        ).stdout
        if tree and not tree.endswith(b"\0"):
            raise CloneSelectError("git_failed")
        for record in tree.split(b"\0"):
            if not record:
                continue
            entry = eligible_record(record)
            if entry is not None and entry.path == path:
                file = read_blob(runner, repository, entry)
                if file is not None:
                    return file
                break
        raise CloneSelectError("not_found")
