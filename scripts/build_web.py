import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("npm is required; install Node 22", file=sys.stderr)
        return 127
    result = subprocess.run([npm, "run", "build"], cwd=ROOT / "web", timeout=180)
    if result.returncode:
        return result.returncode
    target = ROOT / "app" / "static"
    if target.is_symlink() or target.resolve() != ROOT / "app" / "static":
        raise ValueError("Static output must be inside the checkout")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ROOT / "web" / "dist", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
