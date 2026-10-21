import os
import re
import shutil
import subprocess
from pathlib import Path

from ._process import run_owned
from .contracts import CloneSelectError, ErrorCode

_ESSENTIALS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
}


def failure_code(stderr: bytes) -> ErrorCode:
    message = stderr.lower()
    if any(
        part in message
        for part in (
            b"repository not found",
            b"returned error: 404",
            b"could not read username",
            b"authentication failed",
            b"does not appear to be a git repository",
        )
    ):
        return "not_found"
    if any(
        part in message
        for part in (
            b"couldn't find remote ref head",
            b"could not resolve head",
        )
    ):
        return "empty_repository"
    if any(
        part in message
        for part in (
            b"could not resolve host",
            b"failed to connect",
            b"unable to access",
            b"connection reset",
            b"connection timed out",
            b"ssl certificate",
            b"tls",
            b"remote end hung up",
            b"early eof",
        )
    ):
        return "network"
    return "git_failed"


class GitRunner:
    def __init__(self, directory: Path, timeout: float, *, local: bool) -> None:
        executable = shutil.which("git")
        if executable is None:
            raise CloneSelectError("git_unavailable")
        self.executable = str(Path(executable).resolve())
        self.directory = directory
        self.timeout = timeout
        home = directory / "home"
        hooks = directory / "hooks"
        home.mkdir()
        hooks.mkdir()
        config = directory / "config"
        config.touch()
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _ESSENTIALS
        }
        self.environment.update(
            LC_ALL="C",
            LANG="C",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(config),
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="Never",
            GIT_NO_REPLACE_OBJECTS="1",
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_CONFIG_HOME=str(home),
            APPDATA=str(home),
            LOCALAPPDATA=str(home),
        )
        settings = {
            "core.hooksPath": str(hooks),
            "core.fsmonitor": "false",
            "credential.helper": "",
            "credential.interactive": "false",
            "submodule.recurse": "false",
            "fetch.recurseSubmodules": "false",
            "gc.auto": "0",
            "maintenance.auto": "false",
            "fetch.writeCommitGraph": "false",
            "protocol.allow": "never",
            "protocol.https.allow": "always",
            "http.followRedirects": "false",
            "http.sslVerify": "true",
        }
        if local:
            settings["protocol.file.allow"] = "always"
        self.options = [
            part for key, value in settings.items() for part in ("-c", f"{key}={value}")
        ]
        version = self.run("--version").stdout
        match = re.fullmatch(rb"git version (\d+)\.(\d+)\.[^\r\n]+\s*", version)
        if match is None or tuple(map(int, match.groups())) < (2, 47):
            raise CloneSelectError("git_unavailable")

    def run(
        self,
        *arguments: str,
        git_dir: Path | None = None,
        allowed_codes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[bytes]:
        argv = [self.executable, *self.options]
        if git_dir is not None:
            argv.extend(["--git-dir", str(git_dir)])
        argv.extend(arguments)
        try:
            result = run_owned(
                argv,
                cwd=self.directory,
                env=self.environment,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise CloneSelectError("timeout") from None
        except FileNotFoundError:
            raise CloneSelectError("git_unavailable") from None
        except (OSError, subprocess.SubprocessError):
            raise CloneSelectError("git_failed") from None
        if result.returncode not in allowed_codes:
            raise CloneSelectError(failure_code(result.stderr)) from None
        return result


def object_id(raw: bytes) -> str:
    if re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", raw) is None:
        raise CloneSelectError("git_failed")
    return raw.decode("ascii")
