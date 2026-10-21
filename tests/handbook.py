"""Offline handbook repositories for tests and runtime journeys.

Pass a test-owned temporary directory (for example pytest's ``tmp_path``).
The caller owns cleanup; each call allocates a new child repository. Fixture
files are copied as data and are never imported or executed.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "handbook-repo"
ORIGIN = "https://github.com/tka-fixtures/handbook"


def git(repository: Path, *arguments: str) -> str:
    """Run a bounded local Git command without ambient config or hooks."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *arguments],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return result.stdout.strip()


def create_handbook_repo(tmp_path: Path) -> Path:
    """Copy the handbook into a fresh committed repo under a test-owned path."""
    if tmp_path.resolve().is_relative_to(FIXTURE.resolve()):
        raise ValueError("Temporary repositories must be outside the source fixture")
    repository = Path(tempfile.mkdtemp(prefix="handbook-", dir=tmp_path))
    shutil.copytree(FIXTURE, repository, dirs_exist_ok=True)
    git(repository, "init", "--initial-branch=main", "--template=")
    for key, value in {
        "user.name": "Handbook Test",
        "user.email": "handbook@example.invalid",
        "commit.gpgsign": "false",
        "core.autocrlf": "false",
        "core.hooksPath": os.devnull,
    }.items():
        git(repository, "config", "--local", key, value)
    # Include vendored/binary fixtures even when the caller ignores those paths.
    git(repository, "add", "--force", ".")
    git(repository, "commit", "-m", "Initial handbook")
    git(repository, "remote", "add", "origin", ORIGIN)
    return repository
