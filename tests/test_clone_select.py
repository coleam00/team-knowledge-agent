import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.config import Settings
from app.ingest import (
    CloneLimits,
    CloneSelectError,
    SelectedFile,
    SourceSpec,
    _ownership,
    _process,
    clone_and_select,
)
from app.ingest import git as git_module
from app.ingest import repository as repository_module
from app.ingest.git import GitRunner
from app.ingest.repository import canonical_url
from app.ingest.selection import path_language, select_files
from tests.handbook import ORIGIN, create_handbook_repo, git

LIMITS = CloneLimits(100, 10_000_000, 30)
PATHS = [
    "README.md",
    "docs/deploy.md",
    "docs/legacy-deploy.md",
    "docs/onboarding.md",
    "src/keys.py",
]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(None, tmp_path / "data", allow_local_sources=True)


@pytest.fixture
def source(tmp_path: Path) -> SourceSpec:
    return SourceSpec(uuid4(), str(create_handbook_repo(tmp_path)))


def commit(repository: Path) -> None:
    git(repository, "add", "--force", ".")
    git(repository, "commit", "--allow-empty", "-m", "Change test data")


def replace_files(source: SourceSpec, files: dict[str, bytes]) -> Path:
    repository = Path(source.location)
    git(repository, "rm", "-r", "--force", ".")
    for name, data in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    commit(repository)
    return repository


def store(settings: Settings, source: SourceSpec) -> Path:
    return settings.data_dir / "repos" / source.source_id.hex


def test_handbook_metadata_reuse_and_updates(
    source: SourceSpec, settings: Settings
) -> None:
    repository = Path(source.location)
    before = {name: (repository / name).read_bytes() for name in PATHS}
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    assert result.source_id == source.source_id
    assert result.repo_url == ORIGIN
    assert result.commit_sha == git(repository, "rev-parse", "HEAD")
    assert [file.path for file in result.files] == PATHS
    for file in result.files:
        assert file.blob_sha == git(repository, "rev-parse", f"HEAD:{file.path}")
        assert file.content.encode("utf-8") == before[file.path]
        assert file.size_bytes == len(before[file.path])
        assert file.language == ("python" if file.path.endswith(".py") else "markdown")
    assert clone_and_select(source, settings=settings, limits=LIMITS) == result
    git(repository, "checkout", "-b", "new-default")
    (repository / "README.md").write_text("Uncommitted and ignored", encoding="utf-8")
    assert clone_and_select(source, settings=settings, limits=LIMITS) == result
    (repository / "docs/legacy-deploy.md").unlink()
    (repository / "docs/onboarding.md").rename(repository / "docs/start.md")
    commit(repository)
    updated = clone_and_select(source, settings=settings, limits=LIMITS)
    assert updated.commit_sha != result.commit_sha
    assert [file.path for file in updated.files] == [
        "README.md",
        "docs/deploy.md",
        "docs/start.md",
        "src/keys.py",
    ]
    assert updated.files[0].content == "Uncommitted and ignored"
    assert [file.path for file in result.files] == PATHS
    assert (
        git(
            store(settings, source) / "repository.git",
            "cat-file",
            "-t",
            result.commit_sha,
        )
        == "commit"
    )
    assert git(repository, "status", "--porcelain") == ""
    second = SourceSpec(uuid4(), source.location)
    clone_and_select(second, settings=settings, limits=LIMITS)
    assert store(settings, second) != store(settings, source)
    assert store(settings, second).is_dir()


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("README", "text"),
        ("docs/rEaDmE", "text"),
        ("README.MD", "markdown"),
        ("guide.markdown", "markdown"),
        ("a.rst", "rst"),
        ("a.txt", "text"),
        ("a.py", "python"),
        ("a.pyi", "python"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.mjs", "javascript"),
        ("a.cjs", "javascript"),
        ("a.ts", "typescript"),
        ("a.tsx", "typescript"),
        ("a.go", "go"),
        ("a.java", "java"),
        ("a.c", "c"),
        ("a.h", "c"),
        *[(f"a.{ext}", "cpp") for ext in ("cc", "cpp", "cxx", "hh", "hpp", "hxx")],
        ("a.rs", "rust"),
        ("a.rb", "ruby"),
        *[(f"a.{ext}", "shell") for ext in ("sh", "bash", "zsh")],
        ("a.sql", "sql"),
        ("a.css", "css"),
        ("a.html", "html"),
        ("a.htm", "html"),
        ("builder/guide.md", "markdown"),
        ("myvendor/a.py", "python"),
        ("docs/spaces and café.md", "markdown"),
        ("docs/a:b.md", "markdown"),
    ],
)
def test_path_inclusions(path: str, language: str) -> None:
    assert path_language(path) == language


@pytest.mark.parametrize(
    "path",
    [
        *[
            f"docs/{directory}/README.md"
            for directory in (
                ".git",
                "node_modules",
                "vendor",
                "dist",
                "build",
                ".venv",
                "__pycache__",
                "VENDOR",
            )
        ],
        *[
            f"a{suffix}"
            for suffix in (".min.js", ".min.mjs", ".min.cjs", ".min.css", ".MIN.JS")
        ],
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "bun.lockb",
        "go.sum",
        "packages.lock.json",
        "composer.lock",
        "yarn.lock",
        "Cargo.lock",
        "uv.lock",
        "Gemfile.lock",
        "Pipfile.lock",
        "poetry.lock",
        "bun.lock",
        "README.lock",
        "README.png",
        "a.png",
        "a.exe",
        "",
        "/a.md",
        "../a.md",
        "a/../b.md",
        "./a.md",
        "a//b.md",
        "a\\b.md",
        "C:/a.md",
        "C:a.md",
        "a\nb.md",
        "a\x7f.md",
    ],
)
def test_path_exclusions(path: str) -> None:
    assert path_language(path) is None


def test_text_boundaries_and_preservation(
    source: SourceSpec, settings: Settings
) -> None:
    contents = {
        "limit.txt": b"x" * 1_000_000,
        "unicode-limit.md": b"\xc3\xa9" * 500_000,
        "oversize.txt": b"x" * 1_000_001,
        "utf8.txt": b"\xff",
        "binary.py": b"abc\0def",
        "control.md": b"a\x0bb",
        "del.md": b"a\x7fb",
        "empty.md": b"",
        "docs/spaces and café.md": b"\xef\xbb\xbf# Heading\r\n\t\f\n",
    }
    replace_files(source, contents)
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    assert [file.path for file in result.files] == [
        "docs/spaces and café.md",
        "empty.md",
        "limit.txt",
        "unicode-limit.md",
    ]
    for file in result.files:
        assert file.content.encode("utf-8") == contents[file.path]
        assert file.size_bytes == len(contents[file.path])


def test_limits_and_failed_first_store_cleanup(
    source: SourceSpec, settings: Settings
) -> None:
    replace_files(source, {"1.md": b"ab", "2.py": b"c", "3.txt": b"d", "bad.py": b"\0"})
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=CloneLimits(2, 100, 30))
    assert error.value.code == "file_count_limit"
    assert list((settings.data_dir / "repos").iterdir()) == []
    replace_files(source, {"1.md": b"ab", "2.py": b"c", "bad.py": b"\0"})
    result = clone_and_select(source, settings=settings, limits=CloneLimits(2, 3, 30))
    assert len(result.files) == 2
    marker = (store(settings, source) / "source.json").read_bytes()
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=CloneLimits(2, 2, 30))
    assert error.value.code == "total_bytes_limit"
    assert (store(settings, source) / "source.json").read_bytes() == marker
    assert not store(settings, source).with_suffix(".lock").exists()


def test_aggregate_limit_counts_utf8_bytes(
    source: SourceSpec, settings: Settings
) -> None:
    contents = {"a.md": b"\xc3\xa9", "b.py": b"\xc3\xa9"}
    replace_files(source, contents)
    result = clone_and_select(source, settings=settings, limits=CloneLimits(2, 4, 30))
    assert {
        file.path: file.content.encode("utf-8") for file in result.files
    } == contents
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=CloneLimits(2, 3, 30))
    assert error.value.code == "total_bytes_limit"


@pytest.mark.parametrize("reuse", [False, True])
def test_local_commondir_cannot_read_external_objects(
    source: SourceSpec, settings: Settings, tmp_path: Path, reuse: bool
) -> None:
    if reuse:
        clone_and_select(source, settings=settings, limits=LIMITS)
    external = create_handbook_repo(tmp_path)
    (external / "README.md").write_bytes(b"external sentinel")
    commit(external)
    common = tmp_path / "common"
    shutil.copytree(external / ".git", common, ignore=shutil.ignore_patterns("objects"))
    link = common / "objects"
    target = external / ".git/objects"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            timeout=5,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        (Path(source.location) / ".git/commondir").write_text(
            common.as_posix() + "\n", encoding="utf-8"
        )
        with pytest.raises(CloneSelectError) as error:
            clone_and_select(source, settings=settings, limits=LIMITS)
        assert error.value.code == "invalid_local_repository"
        assert (external / "README.md").read_bytes() == b"external sentinel"
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_sha256_initial_retry_and_reuse(tmp_path: Path, settings: Settings) -> None:
    repository = tmp_path / "sha256"
    repository.mkdir()
    git(repository, "init", "--object-format=sha256", "--template=")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "commit.gpgsign", "false")
    git(repository, "remote", "add", "origin", ORIGIN)
    (repository / "README.md").write_bytes(b"sha256 content")
    commit(repository)
    source = SourceSpec(uuid4(), str(repository))
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=CloneLimits(1, 1, 30))
    assert error.value.code == "total_bytes_limit"
    assert list((settings.data_dir / "repos").iterdir()) == []
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    assert result.repo_url == ORIGIN
    assert result.source_id == source.source_id
    assert result.commit_sha == git(repository, "rev-parse", "HEAD")
    assert len(result.commit_sha) == 64
    assert result.files == (
        SelectedFile(
            "README.md",
            git(repository, "rev-parse", "HEAD:README.md"),
            "markdown",
            14,
            "sha256 content",
        ),
    )
    assert len(result.files[0].blob_sha) == 64
    assert clone_and_select(source, settings=settings, limits=LIMITS) == result


@pytest.mark.parametrize("value", [0, -1, True, 1.1, "2"])
def test_invalid_integer_limits(value: object) -> None:
    with pytest.raises(ValueError):
        CloneLimits(value, 10, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CloneLimits(10, value, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "1"])
def test_invalid_timeout(value: object) -> None:
    with pytest.raises(ValueError):
        CloneLimits(10, 10, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/Owner/Repo", "https://github.com/owner/repo"),
        ("https://github.com/a-b/Repo.git/", "https://github.com/a-b/repo"),
        ("HTTPS://GITHUB.COM/A/b.c_d/", "https://github.com/a/b.c_d"),
    ],
)
def test_public_canonicalization(url: str, expected: str) -> None:
    assert canonical_url(url) == expected


@pytest.mark.parametrize(
    "location",
    [
        "https://user:credential-sentinel@github.com/a/b",
        "https://github.com:443/a/b",
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#x",
        "https://github.com/a/b/tree/main",
        "https://github.com/a/..",
        "https://github.com/a/.git",
        "https://github.com/a/%62",
        "https://github.com/a/b//",
        "https://github.com/a/b\n",
        "https://github.com/a/b\\",
        "https://github.com/-a/b",
        "https://github.com/K/b",
        "git@github.com:a/b",
        "file:///repo",
        "ext::program",
        "relative/path",
    ],
)
def test_invalid_source_never_spawns(
    location: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock(side_effect=AssertionError("Unexpected process"))
    monkeypatch.setattr(git_module, "run_owned", process)
    source = SourceSpec(uuid4(), location)
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_source"
    assert location not in repr(source)
    assert "credential-sentinel" not in "".join(traceback.format_exception(error.value))
    process.assert_not_called()


def test_source_id_cannot_be_a_path() -> None:
    with pytest.raises(CloneSelectError):
        SourceSpec("../../escape", ORIGIN)  # type: ignore[arg-type]


def test_local_gate_missing_and_invalid_layout(
    tmp_path: Path, settings: Settings
) -> None:
    source = SourceSpec(uuid4(), str(tmp_path / "missing"))
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(
            source, settings=Settings(None, settings.data_dir), limits=LIMITS
        )
    assert error.value.code == "local_sources_disabled"
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "not_found"
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(
            SourceSpec(uuid4(), str(tmp_path)), settings=settings, limits=LIMITS
        )
    assert error.value.code == "invalid_local_repository"


@pytest.mark.parametrize(
    "key", ["include.path", "includeIf.gitdir:*.path", "core.alternateRefsCommand"]
)
def test_local_executable_config_rejected(
    source: SourceSpec, settings: Settings, key: str
) -> None:
    git(Path(source.location), "config", "--local", key, "credential-sentinel")
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_local_repository"


@pytest.mark.parametrize(
    "origin", [None, "https://secret@github.com/a/b", "https://example.com/a/b"]
)
def test_local_origin_required_and_safe(
    source: SourceSpec, settings: Settings, origin: str | None
) -> None:
    git(Path(source.location), "remote", "remove", "origin")
    if origin is not None:
        git(Path(source.location), "remote", "add", "origin", origin)
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_local_repository"


def test_multiple_origins_and_alternates_rejected(
    source: SourceSpec, settings: Settings
) -> None:
    repository = Path(source.location)
    git(repository, "config", "--add", "remote.origin.url", ORIGIN)
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_local_repository"
    git(repository, "config", "--replace-all", "remote.origin.url", ORIGIN)
    (repository / ".git/objects/info/alternates").write_text(
        "/unowned", encoding="utf-8"
    )
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_local_repository"


def test_identity_lock_and_corrupt_marker(
    source: SourceSpec, settings: Settings
) -> None:
    clone_and_select(source, settings=settings, limits=LIMITS)
    directory = store(settings, source)
    marker = directory / "source.json"
    original = marker.read_bytes()
    git(
        Path(source.location),
        "remote",
        "set-url",
        "origin",
        "https://github.com/other/repo",
    )
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "source_mismatch"
    assert marker.read_bytes() == original
    lock = directory.with_suffix(".lock")
    lock.mkdir()
    sentinel = lock / "owner"
    sentinel.touch()
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "busy"
    assert sentinel.exists()
    sentinel.unlink()
    lock.rmdir()
    marker.write_text("invalid", encoding="utf-8")
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "invalid_storage"


def test_empty_repository_and_empty_selection(
    source: SourceSpec, settings: Settings
) -> None:
    repository = replace_files(source, {"binary.py": b"\0"})
    assert clone_and_select(source, settings=settings, limits=LIMITS).files == ()
    git(repository, "checkout", "--orphan", "unborn")
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "empty_repository"


def test_every_git_invocation_is_protected(
    source: SourceSpec,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actual = _process.run_owned
    calls: list[list[str]] = []
    poison = tmp_path / "ambient"
    poison.write_text(
        '[url "ext::program"]\n insteadOf = https://github.com/\n', encoding="utf-8"
    )
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_CONFIG_GLOBAL",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "OPENROUTER_API_KEY",
        "HTTPS_PROXY",
        "LD_PRELOAD",
    ):
        monkeypatch.setenv(key, str(poison))

    def observe(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        assert Path(argv[0]).is_absolute()
        assert cwd.is_relative_to(settings.data_dir)
        assert cwd != Path(source.location)
        assert timeout == LIMITS.subprocess_timeout_seconds
        for setting in (
            "core.fsmonitor=false",
            "credential.helper=",
            "credential.interactive=false",
            "submodule.recurse=false",
            "fetch.recurseSubmodules=false",
            "gc.auto=0",
            "maintenance.auto=false",
            "fetch.writeCommitGraph=false",
            "protocol.allow=never",
            "protocol.https.allow=always",
            "protocol.file.allow=always",
            "http.followRedirects=false",
            "http.sslVerify=true",
        ):
            assert setting in argv
            assert argv[argv.index(setting) - 1] == "-c"
        hooks = next(
            value.removeprefix("core.hooksPath=")
            for value in argv
            if value.startswith("core.hooksPath=")
        )
        assert list(Path(hooks).iterdir()) == []
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert env["GCM_INTERACTIVE"] == "Never"
        assert Path(env["GIT_CONFIG_GLOBAL"]).read_bytes() == b""
        assert list(Path(env["HOME"]).iterdir()) == []
        assert str(poison) not in env.values()
        return actual(argv, cwd=cwd, env=env, timeout=timeout)

    monkeypatch.setattr(git_module, "run_owned", observe)
    clone_and_select(source, settings=settings, limits=LIMITS)
    assert any("--version" in call for call in calls)
    assert any("config" in call and "--no-includes" in call for call in calls)
    fetch = next(call for call in calls if "fetch" in call)
    assert fetch[-6:] == [
        "fetch",
        "--force",
        "--no-tags",
        "--no-recurse-submodules",
        source.location,
        "+HEAD:refs/tka/snapshot",
    ]
    assert all(
        "checkout" not in call and "--filters" not in call and "--textconv" not in call
        for call in calls
    )


def test_repository_programs_and_symlinks_never_execute(
    source: SourceSpec,
    settings: Settings,
    tmp_path: Path,
) -> None:
    repository = Path(source.location)
    sentinel = tmp_path / "executed"
    script = repository / "payload.sh"
    script.write_text(
        f'#!/bin/sh\nprintf executed > "{sentinel.as_posix()}"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    command = f'"{script.as_posix()}"'
    hooks = repository / ".git/hooks"
    hooks.mkdir()
    for name in ("pre-upload-pack", "post-checkout", "reference-transaction"):
        (hooks / name).write_bytes(script.read_bytes())
        (hooks / name).chmod(0o755)
    (repository / ".gitattributes").write_text(
        "*.md filter=evil diff=evil\n", encoding="utf-8"
    )
    (repository / ".gitmodules").write_text(
        '[submodule "evil"]\n path = submodule\n url = ext::payload\n', encoding="utf-8"
    )
    commit(repository)
    blob = git(repository, "hash-object", "-w", str(script))
    for name in ("external.md", "internal.md"):
        target = repository / "link-target"
        target.write_text(
            str(tmp_path / "secret.md") if name == "external.md" else "README.md",
            encoding="utf-8",
        )
        link_blob = git(repository, "hash-object", "-w", str(target))
        target.unlink()
        git(
            repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"120000,{link_blob},{name}",
        )
    git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"100755,{blob},executable.sh",
    )
    head = git(repository, "rev-parse", "HEAD")
    git(repository, "update-index", "--add", "--cacheinfo", f"160000,{head},submodule")
    git(repository, "commit", "-m", "Dangerous modes as data")
    for key, value in {
        "core.hooksPath": str(hooks),
        "core.fsmonitor": command,
        "filter.evil.smudge": command,
        "diff.evil.textconv": command,
        "uploadpack.packObjectsHook": command,
    }.items():
        git(repository, "config", "--local", key, value)
    assert not sentinel.exists()
    (tmp_path / "secret.md").write_text("must not read", encoding="utf-8")
    before = {
        path.relative_to(repository).as_posix(): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    paths = [file.path for file in result.files]
    assert "executable.sh" in paths
    assert not {"external.md", "internal.md", "submodule"}.intersection(paths)
    cache = store(settings, source) / "repository.git"
    (cache / "hooks").mkdir(exist_ok=True)
    hook = cache / "hooks/reference-transaction"
    hook.write_bytes(script.read_bytes())
    hook.chmod(0o755)
    assert clone_and_select(source, settings=settings, limits=LIMITS) == result
    assert not sentinel.exists()
    after = {
        path.relative_to(repository).as_posix(): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_selection_uses_captured_commit_when_ref_moves(
    source: SourceSpec,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Path(source.location)
    original = clone_and_select(source, settings=settings, limits=LIMITS)
    replace_files(source, {"new.md": b"new tree"})

    def select_old(
        runner: GitRunner, cache: Path, commit_sha: str, limits: CloneLimits
    ) -> tuple[SelectedFile, ...]:
        assert commit_sha == git(repository, "rev-parse", "HEAD")
        runner.run(
            "update-ref", "refs/tka/snapshot", original.commit_sha, git_dir=cache
        )
        return select_files(runner, cache, commit_sha, limits)

    monkeypatch.setattr(repository_module, "select_files", select_old)
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    assert [file.path for file in result.files] == ["new.md"]
    assert result.files[0].content == "new tree"


@pytest.mark.parametrize(
    ("stderr", "code"),
    [
        (b"remote: Repository not found. credential-sentinel", "not_found"),
        (
            b"fatal: could not read Username for 'https://credential-sentinel@github.com':"
            b" terminal prompts disabled",
            "not_found",
        ),
        (
            b"fatal: unable to access credential-sentinel: "
            b"The requested URL returned error: 404",
            "not_found",
        ),
        (b"fatal: Could not resolve host: credential-sentinel", "network"),
        (b"fatal: Failed to connect credential-sentinel", "network"),
        (b"fatal: SSL certificate problem credential-sentinel", "network"),
        (b"fatal: unexpected failure credential-sentinel", "git_failed"),
    ],
)
def test_remote_failures_are_distinct_and_redacted(
    stderr: bytes,
    code: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actual = _process.run_owned

    def fail_fetch(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        if "fetch" in argv:
            assert "protocol.file.allow=always" not in argv
            assert argv[-2] == ORIGIN + ".git"
            return subprocess.CompletedProcess(argv, 128, b"", stderr)
        return actual(argv, cwd=cwd, env=env, timeout=timeout)

    monkeypatch.setattr(git_module, "run_owned", fail_fetch)
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(SourceSpec(uuid4(), ORIGIN), settings=settings, limits=LIMITS)
    assert error.value.code == code
    display = (
        str(error.value)
        + repr(error.value)
        + "".join(traceback.format_exception(error.value))
        + caplog.text
    )
    assert "credential-sentinel" not in display
    assert list((settings.data_dir / "repos").iterdir()) == []


def test_timeout_terminates_owned_command(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        _process.run_owned(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=0.01,
        )


def test_timeout_terminates_descendants(tmp_path: Path) -> None:
    sentinel = tmp_path / "late-write"
    started = tmp_path / "started"
    release = tmp_path / "release"
    child = f"""
import time
from pathlib import Path
Path({str(started)!r}).touch()
while not Path({str(release)!r}).exists():
    time.sleep(0.01)
Path({str(sentinel)!r}).touch()
"""
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _process.run_owned(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=5,
        )
    assert started.exists(), "Descendant must start for this test to prove cleanup"
    release.touch()
    time.sleep(2)
    assert not sentinel.exists()


def test_git_timeout_and_unavailable_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for failure, code in [
        (subprocess.TimeoutExpired("credential-sentinel", 0.01), "timeout"),
        (FileNotFoundError("credential-sentinel"), "git_unavailable"),
        (OSError("credential-sentinel"), "git_failed"),
    ]:
        directory = tmp_path / code
        directory.mkdir()
        monkeypatch.setattr(git_module, "run_owned", Mock(side_effect=failure))
        with pytest.raises(CloneSelectError) as error:
            GitRunner(directory, 0.01, local=False)
        assert error.value.code == code
        assert "credential-sentinel" not in "".join(
            traceback.format_exception(error.value)
        )


def test_old_git_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        git_module,
        "run_owned",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, b"git version 2.46.9\n", b""
            )
        ),
    )
    with pytest.raises(CloneSelectError) as error:
        GitRunner(tmp_path, 1, local=False)
    assert error.value.code == "git_unavailable"


def test_storage_links_cannot_escape(
    source: SourceSpec,
    settings: Settings,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "preserve"
    sentinel.write_bytes(b"untouched")
    settings.data_dir.mkdir()
    link = settings.data_dir / "repos"
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
        with pytest.raises(CloneSelectError) as error:
            clone_and_select(source, settings=settings, limits=LIMITS)
        assert error.value.code == "invalid_storage"
        assert sentinel.read_bytes() == b"untouched"
        assert list(outside.iterdir()) == [sentinel]
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize(
    "raw_path",
    [
        b"invalid-\xff.md",
        b"newline\nname.md",
        b"tab\tname.md",
        b"../escape.md",
        b"/absolute.md",
        b"a\\b.md",
        b"a//b.md",
        b"a/./b.md",
        b"C:/a.md",
    ],
)
def test_unrepresentable_tree_paths_are_skipped(
    raw_path: bytes, tmp_path: Path
) -> None:
    runner = Mock(spec=GitRunner)
    record = b"100644 blob " + b"a" * 40 + b" 3\t" + raw_path + b"\0"
    runner.run.return_value = subprocess.CompletedProcess([], 0, record, b"")
    assert select_files(runner, tmp_path, "b" * 40, LIMITS) == ()
    runner.run.assert_called_once()


@pytest.mark.parametrize(
    "record",
    [
        b"malformed\0",
        b"100644 blob bad-id 3\tx.md\0",
        b"100644 blob " + b"a" * 40 + b" bad-size\tx.md\0",
        b"100644 blob " + b"a" * 40 + b" -1\tx.md\0",
        b"100644 blob " + b"a" * 40 + b" 3\tx.md\0",
    ],
)
def test_corrupt_tree_or_mismatched_blob_fails(record: bytes, tmp_path: Path) -> None:
    runner = Mock(spec=GitRunner)
    runner.run.side_effect = [
        subprocess.CompletedProcess([], 0, record, b""),
        subprocess.CompletedProcess([], 0, b"wrong length", b""),
    ]
    with pytest.raises(CloneSelectError) as error:
        select_files(runner, tmp_path, "b" * 40, LIMITS)
    assert error.value.code == "git_failed"


def test_local_storage_cannot_write_inside_source(source: SourceSpec) -> None:
    destination = Path(source.location) / "data"
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(
            source,
            settings=Settings(None, destination, allow_local_sources=True),
            limits=LIMITS,
        )
    assert error.value.code == "invalid_storage"
    assert not destination.exists()


def test_git_file_layout_is_rejected(tmp_path: Path, settings: Settings) -> None:
    repository = tmp_path / "worktree"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: ../unowned", encoding="utf-8")
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(
            SourceSpec(uuid4(), str(repository)), settings=settings, limits=LIMITS
        )
    assert error.value.code == "invalid_local_repository"


def test_failed_fetch_preserves_existing_store(
    source: SourceSpec,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = clone_and_select(source, settings=settings, limits=LIMITS)
    directory = store(settings, source)
    before = {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    actual = _process.run_owned

    def fail_fetch(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        if "fetch" in argv:
            return subprocess.CompletedProcess(argv, 128, b"", b"connection reset")
        return actual(argv, cwd=cwd, env=env, timeout=timeout)

    monkeypatch.setattr(git_module, "run_owned", fail_fetch)
    with pytest.raises(CloneSelectError) as error:
        clone_and_select(source, settings=settings, limits=LIMITS)
    assert error.value.code == "network"
    assert {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    } == before
    assert (
        git(directory / "repository.git", "rev-parse", "refs/tka/snapshot")
        == result.commit_sha
    )
    assert not directory.with_suffix(".lock").exists()


def test_success_also_reaps_descendants(tmp_path: Path) -> None:
    sentinel = tmp_path / "late-write"
    child = (
        "import time; from pathlib import Path; time.sleep(2); "
        f"Path({str(sentinel)!r}).touch()"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); print('done')"
    )
    result = _process.run_owned(
        [sys.executable, "-c", parent],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=5,
    )
    assert result.returncode == 0 and result.stdout.strip() == b"done"
    time.sleep(2)
    assert not sentinel.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job ownership")
def test_failed_job_assignment_never_launches_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        monkeypatch.setattr(
            _ownership._Job,
            "assign",
            Mock(side_effect=OSError("assignment failed")),
        )
    sentinel = tmp_path / "should-not-exist"
    command = f"from pathlib import Path; Path({str(sentinel)!r}).touch()"
    with pytest.raises(OSError, match="assignment failed"):
        _process.run_owned(
            [sys.executable, "-c", command],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=5,
        )
    assert not sentinel.exists()
