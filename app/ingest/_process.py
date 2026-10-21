"""Collect Git output under the shared process owner and an injected deadline."""

import subprocess
import tempfile
from pathlib import Path

from process_ownership import ProcessTree


def run_owned(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        tree = ProcessTree(
            argv, cwd, env, stdout=stdout.fileno(), stderr=stderr.fileno()
        )
        try:
            returncode = tree.proc.wait(timeout=timeout)
        finally:
            tree.close()
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(
            argv, returncode, stdout.read(), stderr.read()
        )
