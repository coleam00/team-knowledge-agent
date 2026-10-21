import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("static", "unit"))
    arguments = parser.parse_args()
    npm = shutil.which("npm")
    if npm is None:
        print("npm is required; install Node 22", file=sys.stderr)
        return 127
    python = sys.executable
    commands = (
        [
            [python, "-m", "ruff", "check", "."],
            [python, "-m", "ruff", "format", "--check", "."],
            [python, "-m", "mypy"],
            [npm, "run", "lint", "--prefix", "web"],
            [npm, "run", "typecheck", "--prefix", "web"],
        ]
        if arguments.mode == "static"
        else [
            [python, "-m", "pytest"],
            [npm, "test", "--prefix", "web"],
        ]
    )
    environment = os.environ.copy()
    environment["TKA_MODELS"] = "fake"
    environment.pop("OPENROUTER_API_KEY", None)
    for command in commands:
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, timeout=600)
        except subprocess.TimeoutExpired:
            print(f"Check timed out: {command[0]}", file=sys.stderr)
            return 124
        except OSError:
            print(f"Could not start: {command[0]}", file=sys.stderr)
            return 127
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
