"""Offline handbook repositories for tests and runtime journeys.

Pass a test-owned temporary directory (for example pytest's ``tmp_path``).
The caller owns cleanup; each call allocates a new child repository. Fixture
files are copied as data and are never imported or executed.
"""

import os
import shutil
import tempfile
from pathlib import Path

from app.git import git

FIXTURE = Path(__file__).parent / "fixtures" / "handbook-repo"
ORIGIN = "https://github.com/tka-fixtures/handbook"


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
