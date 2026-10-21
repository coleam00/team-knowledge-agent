"""Own Git and its transport descendants for the entire command lifetime."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform == "win32":
    import ctypes as c
    from ctypes import wintypes as w

    class _Basic(c.Structure):
        _fields_ = [
            ("process_time", c.c_int64),
            ("job_time", c.c_int64),
            ("flags", w.DWORD),
            ("minimum", c.c_size_t),
            ("maximum", c.c_size_t),
            ("active", w.DWORD),
            ("affinity", c.c_size_t),
            ("priority", w.DWORD),
            ("scheduling", w.DWORD),
        ]

    class _Extended(c.Structure):
        _fields_ = [
            ("basic", _Basic),
            ("io", c.c_uint64 * 6),
            ("process_memory", c.c_size_t),
            ("job_memory", c.c_size_t),
            ("peak_process", c.c_size_t),
            ("peak_job", c.c_size_t),
        ]

    class _Accounting(c.Structure):
        _fields_ = [
            ("times", c.c_int64 * 4),
            ("faults", w.DWORD),
            ("total", w.DWORD),
            ("active", w.DWORD),
            ("terminated", w.DWORD),
        ]

    class _Job:
        def __init__(self) -> None:
            self.kernel = c.WinDLL("kernel32", use_last_error=True)
            for name, arguments, result in [
                ("CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE),
                (
                    "SetInformationJobObject",
                    [w.HANDLE, c.c_int, c.c_void_p, w.DWORD],
                    w.BOOL,
                ),
                ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
                ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
                ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
                (
                    "QueryInformationJobObject",
                    [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p],
                    w.BOOL,
                ),
                ("CloseHandle", [w.HANDLE], w.BOOL),
            ]:
                function = getattr(self.kernel, name)
                function.argtypes = arguments
                function.restype = result
            self.handle = self.kernel.CreateJobObjectW(None, None)
            info = _Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.handle:
                raise OSError("Cannot create process job")
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, c.byref(info), c.sizeof(info)
            ):
                self.kernel.CloseHandle(self.handle)
                raise OSError("Cannot configure process job")

        def assign(self, pid: int) -> None:
            handle = self.kernel.OpenProcess(0x0101, False, pid)
            try:
                if not handle or not self.kernel.AssignProcessToJobObject(
                    self.handle, handle
                ):
                    raise OSError("Cannot assign process job")
            finally:
                if handle:
                    self.kernel.CloseHandle(handle)

        def close(self) -> None:
            try:
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise OSError("Cannot terminate process job")
                deadline = time.monotonic() + 15
                while True:
                    info = _Accounting()
                    if not self.kernel.QueryInformationJobObject(
                        self.handle, 1, c.byref(info), c.sizeof(info), None
                    ):
                        raise OSError("Cannot verify process termination")
                    if info.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise OSError("Process job failed to terminate")
                    time.sleep(0.01)
            finally:
                self.kernel.CloseHandle(self.handle)


def run_owned(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        if sys.platform == "win32":
            job = _Job()
        else:
            job = None
        process: subprocess.Popen[bytes] | None = None
        try:
            # The trusted worker cannot launch Git until ownership is established.
            process = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), *argv],
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
            if job is not None:
                job.assign(process.pid)
            assert process.stdin is not None
            process.stdin.write(b"1")
            process.stdin.close()
            returncode = process.wait(timeout=timeout)
        finally:
            try:
                if job is not None:
                    job.close()
                elif sys.platform != "win32":
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=15)
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(
            argv, returncode, stdout.read(), stderr.read()
        )


if __name__ == "__main__":
    if sys.stdin.buffer.read(1) != b"1":
        raise SystemExit(1)
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, shell=False)
    raise SystemExit(child.wait())
