import os
import subprocess
from pathlib import Path


def git(repository: Path, *arguments: str, timeout: int = 30) -> str:
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
        timeout=timeout,
    )
    return result.stdout.removesuffix("\n")
