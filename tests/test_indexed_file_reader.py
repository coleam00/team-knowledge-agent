import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.config import Settings
from app.git import git
from app.ingest import CloneSelectError, SelectedFile, SourceSpec, clone_and_select
from app.ingest.contracts import ErrorCode
from app.ingest.git import GitRunner
from app.ingest.repository import read_cached_file
from tests.handbook import ORIGIN, create_handbook_repo
from tests.test_clone_select import LIMITS, commit


@dataclass
class Cache:
    source: SourceSpec
    settings: Settings
    commit: str

    @property
    def directory(self) -> Path:
        return self.settings.data_dir / "repos" / self.source.source_id.hex

    def read(self, path: str = "README.md", commit: str | None = None) -> SelectedFile:
        return read_cached_file(
            self.source,
            repo_url=ORIGIN,
            commit=commit or self.commit,
            path=path,
            settings=self.settings,
        )


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    source = SourceSpec(uuid4(), str(create_handbook_repo(tmp_path)))
    settings = Settings(None, tmp_path / "data", allow_local_sources=True)
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    return Cache(source, settings, result.commit_sha)


def assert_error(cache: Cache, code: str, **kwargs: str) -> None:
    with pytest.raises(CloneSelectError) as error:
        cache.read(**kwargs)
    assert error.value.code == code
    assert not cache.directory.with_suffix(".lock").exists()


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_pinned_cache_only_reads(cache: Cache, monkeypatch: pytest.MonkeyPatch) -> None:
    before = cache.read("docs/deploy.md")
    repository = Path(cache.source.location)
    (repository / "docs/deploy.md").write_bytes(b"New deployment\n")
    (repository / "docs/legacy-deploy.md").unlink()
    (repository / "docs/onboarding.md").rename(repository / "docs/start.md")
    commit(repository)
    updated = clone_and_select(cache.source, settings=cache.settings, limits=LIMITS)
    (repository / "docs/deploy.md").write_bytes(b"Uncommitted")
    repository.rename(repository.with_name("moved-upstream"))
    stored = snapshot(cache.directory)
    run = GitRunner.run
    calls: list[tuple[str, ...]] = []

    def observe(
        self: GitRunner,
        *args: str,
        git_dir: Path | None = None,
        allowed_codes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        assert self.environment["GIT_NO_LAZY_FETCH"] == "1"
        assert "protocol.https.allow=never" in self.options
        assert "protocol.file.allow=never" in self.options
        assert self.timeout == 30
        return run(self, *args, git_dir=git_dir, allowed_codes=allowed_codes)

    monkeypatch.setattr(GitRunner, "run", observe)
    monkeypatch.setenv("GIT_DIR", str(repository))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(repository))
    assert cache.read("docs/deploy.md") == before
    assert (
        cache.read("docs/deploy.md", updated.commit_sha).content == "New deployment\n"
    )
    assert cache.read("docs/start.md", updated.commit_sha).content
    for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
        assert cache.read(path).content
        assert_error(cache, "not_found", path=path, commit=updated.commit_sha)
    assert all(
        args[0] in {"--version", "config", "cat-file", "ls-tree"} for args in calls
    )
    assert all("docs/deploy.md" not in args for args in calls)
    assert snapshot(cache.directory) == stored


def test_objects_identity_and_lock(cache: Cache) -> None:
    stored = snapshot(cache.directory)
    for value in ("HEAD", cache.commit + ":README.md", "-x", "A" * 40):
        assert_error(cache, "git_failed", commit=value)
    assert_error(cache, "not_found", commit="0" * 40)
    blob = cache.read().blob_sha
    assert_error(cache, "git_failed", commit=blob)
    assert_error(cache, "not_found", path="README.MD")
    assert_error(cache, "not_found", path="docs")
    marker = cache.directory / "source.json"
    original = marker.read_bytes()
    for field in ("source_id", "location", "kind", "repo_url"):
        data = json.loads(original)
        data[field] = "wrong"
        marker.write_text(json.dumps(data))
        assert_error(cache, "source_mismatch")
    marker.write_text("corrupt")
    assert_error(cache, "invalid_storage")
    marker.write_bytes(original)
    lock = cache.directory.with_suffix(".lock")
    lock.mkdir()
    sentinel = lock / "owner"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(CloneSelectError) as error:
        cache.read()
    assert error.value.code == "busy"
    assert sentinel.read_bytes() == b"preserve"
    sentinel.unlink()
    lock.rmdir()
    assert snapshot(cache.directory) == stored
    disabled = replace(
        cache, settings=replace(cache.settings, allow_local_sources=False)
    )
    assert_error(disabled, "local_sources_disabled")
    missing = replace(cache, source=SourceSpec(uuid4(), cache.source.location))
    assert_error(missing, "not_found")
    assert not missing.directory.exists()


def test_external_storage_and_configuration(cache: Cache, tmp_path: Path) -> None:
    repository = cache.directory / "repository.git"
    for name in (
        "commondir",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ):
        path = repository / name
        path.write_text(str(tmp_path))
        assert_error(cache, "invalid_storage")
        path.unlink()
    config = repository / "config"
    original = config.read_bytes()
    for key in (
        "include.path",
        "includeIf.gitdir:/.path",
        "core.alternateRefsCommand",
        "extensions.partialClone",
        "extensions.worktreeConfig",
        "core.worktree",
        "remote.origin.promisor",
        "remote.origin.partialCloneFilter",
    ):
        git(repository, "config", "--local", key, str(tmp_path / "outside"))
        assert_error(cache, "invalid_storage")
        config.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret.md"
    sentinel.write_bytes(b"outside secret")
    link = repository / "objects" / "linked"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
            timeout=5,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        assert_error(cache, "invalid_storage")
        assert sentinel.read_bytes() == b"outside secret"
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_eligibility_and_no_execution(cache: Cache, tmp_path: Path) -> None:
    repository = Path(cache.source.location)
    contents = {
        "limit.txt": b"x" * 1_000_000,
        "oversize.txt": b"x" * 1_000_001,
        "binary.md": b"secret\0",
        "invalid.md": b"\xff",
        "empty.md": b"",
        "vendor/private.md": b"excluded",
        "a.min.js": b"excluded",
        "unknown.xyz": b"excluded",
        "docs/spaces and caf\u00e9.md": b"unicode",
        "docs/literal%2f.md": b"percent",
    }
    for name, data in contents.items():
        path = repository / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(data)
    sentinel = tmp_path / "executed"
    script = repository / "payload.sh"
    script.write_text(
        f'#!/bin/sh\nprintf executed > "{sentinel.as_posix()}"\n', newline="\n"
    )
    (repository / ".gitattributes").write_text("*.md filter=evil diff=evil\n")
    commit(repository)
    blob = git(repository, "hash-object", "-w", str(script))
    git(repository, "config", "core.protectNTFS", "false")
    for mode, name in (
        ("100755", "executable.sh"),
        ("120000", "external.md"),
        ("100644", "docs/a:b*.md"),
    ):
        git(repository, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{name}")
    git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{cache.commit},submodule",
    )
    git(repository, "commit", "-m", "File modes")
    updated = clone_and_select(cache.source, settings=cache.settings, limits=LIMITS)
    cache.commit = updated.commit_sha
    bare = cache.directory / "repository.git"
    for key in (
        "core.fsmonitor",
        "filter.evil.smudge",
        "diff.evil.textconv",
        "core.hooksPath",
    ):
        git(bare, "config", "--local", key, str(script))
    before = snapshot(cache.directory)
    for name in (
        "oversize.txt",
        "binary.md",
        "invalid.md",
        "vendor/private.md",
        "a.min.js",
        "unknown.xyz",
        "external.md",
        "submodule",
        "docs/a:bX.md",
    ):
        assert_error(cache, "not_found", path=name)
    for name in (
        "limit.txt",
        "empty.md",
        "docs/spaces and caf\u00e9.md",
        "docs/literal%2f.md",
    ):
        assert cache.read(name).content.encode() == contents[name]
    assert cache.read("executable.sh").content == script.read_text()
    assert cache.read("docs/a:b*.md").content == script.read_text()
    assert not sentinel.exists()
    assert snapshot(cache.directory) == before


def test_reader_failures_are_safe(
    cache: Cache, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = GitRunner.run
    before = snapshot(cache.directory)
    codes: tuple[ErrorCode, ...] = ("git_unavailable", "timeout", "git_failed")
    for code in codes:
        with monkeypatch.context() as patch:
            patch.setattr(GitRunner, "run", Mock(side_effect=CloneSelectError(code)))
            with pytest.raises(CloneSelectError) as error:
                cache.read()
            assert error.value.code == code
        assert not cache.directory.with_suffix(".lock").exists()
    for payload in (
        b"bad\0",
        b"100644 blob " + b"a" * 40 + b" 2\tREADME.md\0",
        b"unterminated",
    ):

        def corrupt(
            self: GitRunner,
            *args: str,
            git_dir: Path | None = None,
            allowed_codes: tuple[int, ...] = (0,),
            tree_output: bytes = payload,
        ) -> subprocess.CompletedProcess[bytes]:
            if args[0] == "ls-tree":
                return subprocess.CompletedProcess(args, 0, tree_output, b"")
            return run(self, *args, git_dir=git_dir, allowed_codes=allowed_codes)

        monkeypatch.setattr(GitRunner, "run", corrupt)
        assert_error(cache, "git_failed")
    assert snapshot(cache.directory) == before


def test_sha256(tmp_path: Path) -> None:
    repository = tmp_path / "sha256"
    repository.mkdir()
    git(repository, "init", "--object-format=sha256", "--template=")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(repository, "config", key, value)
    git(repository, "remote", "add", "origin", ORIGIN)
    (repository / "README.md").write_bytes(b"sha256\n")
    commit(repository)
    source = SourceSpec(uuid4(), str(repository))
    settings = Settings(None, tmp_path / "data", allow_local_sources=True)
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    assert len(result.commit_sha) == 64
    assert Cache(source, settings, result.commit_sha).read() == result.files[0]
