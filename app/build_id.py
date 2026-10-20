import os
import subprocess
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def resolve_build_id() -> str:
    if "FACTORY_RUNTIME_CANDIDATE" in os.environ:
        return os.environ["FACTORY_RUNTIME_CANDIDATE"]
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=SOURCE_ROOT,
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=3,
        )
        if Path(root.stdout.rstrip("\r\n")).resolve() != SOURCE_ROOT:
            return "unknown"
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SOURCE_ROOT,
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=3,
        )
        return commit.stdout.rstrip("\r\n") or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
