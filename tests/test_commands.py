import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import build_web, check


@pytest.mark.parametrize(
    ("mode", "codes"),
    [
        ("unit", [4]),
        ("unit", [0, 7]),
        ("static", [0, 0, 0, 0, 9]),
        ("unit", [5]),
        ("unit", [0, 0]),
    ],
)
def test_runner_propagates_backend_and_frontend_failures(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    codes: list[int],
) -> None:
    monkeypatch.setattr(sys, "argv", ["check.py", mode])
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-tests")
    monkeypatch.setattr(shutil, "which", lambda _: "C:/Program Files/nodejs/npm.cmd")
    run = Mock(side_effect=[subprocess.CompletedProcess([], code) for code in codes])
    monkeypatch.setattr(subprocess, "run", run)
    assert check.main() == codes[-1]
    assert run.call_count == len(codes)
    for call in run.call_args_list:
        assert "OPENROUTER_API_KEY" not in call.kwargs["env"]
        assert call.kwargs["env"]["TKA_MODELS"] == "fake"
    if len(codes) > 1 and mode == "unit":
        assert run.call_args_list[-1].args[0][0] == "C:/Program Files/nodejs/npm.cmd"


def test_missing_npm_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["check.py", "unit"])
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert check.main() == 127


def test_failed_build_preserves_static_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "app" / "static"
    target.mkdir(parents=True)
    (target / "index.html").write_text("last good build")
    monkeypatch.setattr(build_web, "ROOT", tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: "npm.cmd")
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 7)),
    )
    assert build_web.main() == 7
    assert (target / "index.html").read_text() == "last good build"
