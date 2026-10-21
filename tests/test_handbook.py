import ast
from pathlib import Path

import pytest

from app.git import git
from tests.handbook import FIXTURE, ORIGIN, create_handbook_repo

FILES = {
    "README.md",
    "docs/deploy.md",
    "docs/legacy-deploy.md",
    "docs/onboarding.md",
    "src/keys.py",
    "assets/logo.png",
    "node_modules/left-pad/index.js",
}


def fixture_bytes() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_bytes()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }


def test_fixture_contains_journey_facts_and_skip_candidates() -> None:
    contents = fixture_bytes()
    assert set(contents) == FILES
    assert not any(path.name == ".git" for path in FIXTURE.rglob("*"))
    assert (
        b"Deploys are frozen on Fridays after 14:00 UTC." in contents["docs/deploy.md"]
    )
    legacy = b"Legacy deploys use the copper heron switch."
    assert contents["docs/legacy-deploy.md"].count(legacy) == 1
    assert sum(data.count(legacy) for data in contents.values()) == 1
    assert all(b"parental" not in data.lower() for data in contents.values())
    assert contents["assets/logo.png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert b"\x00" in contents["assets/logo.png"]
    # Parse source as data; never import or execute the fixture.
    module = ast.parse(contents["src/keys.py"])
    rotation = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "rotate_api_keys"
    )
    docstring = ast.get_docstring(rotation)
    assert docstring is not None
    assert "replacement" in docstring and "revoking" in docstring


def test_repositories_are_isolated_committed_copies(tmp_path: Path) -> None:
    original = fixture_bytes()
    first = create_handbook_repo(tmp_path)
    second = create_handbook_repo(tmp_path)
    assert first != second
    for repository in (first, second):
        assert repository.parent == tmp_path
        assert (repository / ".git").is_dir()
        assert git(repository, "remote", "get-url", "origin") == ORIGIN
        assert git(repository, "status", "--porcelain") == ""
        assert set(git(repository, "ls-files").splitlines()) == FILES
        assert git(repository, "rev-list", "--count", "HEAD") == "1"
        assert git(repository, "log", "-1", "--format=%an <%ae>") == (
            "Handbook Test <handbook@example.invalid>"
        )
        for name, data in original.items():
            assert (repository / name).read_bytes() == data
    (first / "docs/deploy.md").write_text("Changed only here\n", encoding="utf-8")
    assert (second / "docs/deploy.md").read_bytes() == original["docs/deploy.md"]
    assert fixture_bytes() == original


def test_edit_delete_and_rename_preserve_original_commit(tmp_path: Path) -> None:
    original = fixture_bytes()
    repository = create_handbook_repo(tmp_path)
    before = git(repository, "rev-parse", "HEAD")
    original_blobs = {
        name: git(repository, "hash-object", "--no-filters", name)
        for name in ("docs/deploy.md", "docs/legacy-deploy.md", "docs/onboarding.md")
    }
    updated = "# Deployment guide\n\nDeploys are frozen on Fridays after 12:00 UTC.\n"
    (repository / "docs/deploy.md").write_text(updated, encoding="utf-8", newline="\n")
    (repository / "docs/legacy-deploy.md").unlink()
    (repository / "docs/onboarding.md").rename(repository / "docs/getting-started.md")
    git(repository, "add", "--all")
    git(repository, "commit", "-m", "Update deploy freeze and onboarding")
    after = git(repository, "rev-parse", "HEAD")
    assert before != after
    assert git(repository, "rev-parse", "HEAD^") == before
    assert git(repository, "rev-list", "--count", "HEAD") == "2"
    assert git(repository, "status", "--porcelain") == ""
    for name, blob in original_blobs.items():
        assert git(repository, "rev-parse", f"{before}:{name}") == blob
    assert git(repository, "show", f"{after}:docs/deploy.md") == updated.strip()
    assert (
        git(repository, "rev-parse", f"{after}:docs/getting-started.md")
        == original_blobs["docs/onboarding.md"]
    )
    assert set(git(repository, "ls-tree", "-r", "--name-only", after).splitlines()) == (
        FILES - {"docs/legacy-deploy.md", "docs/onboarding.md"}
    ) | {"docs/getting-started.md"}
    assert "copper heron" not in git(repository, "show", f"{after}:docs/deploy.md")
    assert fixture_bytes() == original


def test_ambient_git_settings_and_hooks_cannot_redirect_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trap = tmp_path / "ambient.git"
    config = tmp_path / "ambient.config"
    config.write_text(
        "[commit]\n\tgpgsign = true\n[user]\n\tname = Ambient\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_DIR", str(trap))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ambient")
    repository = create_handbook_repo(tmp_path)
    assert not trap.exists()
    assert git(repository, "log", "-1", "--format=%an") == "Handbook Test"
    hook = repository / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    # A failing hook must not run even on subsequent fixture commits.
    git(repository, "commit", "--allow-empty", "-m", "Hooks remain disabled")
    assert git(repository, "rev-list", "--count", "HEAD") == "2"


def test_source_fixture_cannot_be_used_as_temporary_directory() -> None:
    with pytest.raises(ValueError, match="outside the source fixture"):
        create_handbook_repo(FIXTURE)
