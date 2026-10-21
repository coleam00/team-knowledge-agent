import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import build_id
from app.config import Settings


@pytest.mark.parametrize(
    ("data", "state", "expected"),
    [
        ("clones", "state", "clones"),
        (None, "state", "state"),
        ("", "state", "state"),
        (None, None, ".tka-data"),
        ("", "", ".tka-data"),
    ],
)
def test_data_directory_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    data: str | None,
    state: str | None,
    expected: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name, value in [("TKA_DATA_DIR", data), ("FACTORY_RUNTIME_STATE", state)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert Settings.from_env().data_dir == tmp_path / expected


def test_settings_do_not_expose_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "credential-sentinel")
    assert "credential-sentinel" not in repr(Settings.from_env())


@pytest.mark.parametrize(
    "candidate", ["candidate-123", "  candidate\n", "", "révision"]
)
def test_candidate_is_exact(monkeypatch: pytest.MonkeyPatch, candidate: str) -> None:
    monkeypatch.setenv("FACTORY_RUNTIME_CANDIDATE", candidate)
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=AssertionError("Git called"))
    )
    assert build_id.resolve_build_id() == candidate


def test_real_git_fallback_ignores_obsolete_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=build_id.SOURCE_ROOT, text=True, timeout=3
    ).strip()
    monkeypatch.setenv("TKA_BUILD_ID", "obsolete")
    assert build_id.resolve_build_id() == expected


@pytest.mark.parametrize("directory", ["checkout with spaces", "caf\u00e9", "\u0101"])
def test_git_identity_in_non_ascii_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, directory: str
) -> None:
    checkout = tmp_path / directory
    checkout.mkdir()
    subprocess.run(
        ["git", "init", str(checkout)], check=True, capture_output=True, timeout=3
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "Identity fixture",
        ],
        cwd=checkout,
        check=True,
        capture_output=True,
        timeout=3,
    )
    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, encoding="utf-8", timeout=3
    ).strip()
    monkeypatch.setattr(build_id, "SOURCE_ROOT", checkout)
    assert build_id.resolve_build_id() == expected


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.CalledProcessError(1, "git"),
        subprocess.TimeoutExpired("git", 3),
    ],
)
def test_unavailable_git_is_unknown(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    assert build_id.resolve_build_id() == "unknown"


def test_archive_without_git_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(build_id, "SOURCE_ROOT", tmp_path)
    assert build_id.resolve_build_id() == "unknown"


def test_enclosing_repository_is_not_our_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", str(tmp_path)], check=True, capture_output=True, timeout=3
    )
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setattr(build_id, "SOURCE_ROOT", archive)
    assert build_id.resolve_build_id() == "unknown"
