import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import process_ownership
from process_ownership import ProcessTree


def test_pollable_process_with_explicit_environment_and_output(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    command = (
        "import os, sys, time; from pathlib import Path; "
        "assert sys.stdin.buffer.read() == b''; "
        "sys.stdout.buffer.write(b'out\\x00\\xff'); sys.stdout.flush(); "
        "sys.stderr.buffer.write(b'err\\x00\\xfe'); sys.stderr.flush(); "
        "Path('ready').write_text(os.environ['OWNED_VALUE']); "
        "time.sleep(60)"
    )
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        tree = ProcessTree(
            [sys.executable, "-c", command],
            tmp_path,
            {**os.environ, "OWNED_VALUE": "isolated"},
            stdout=stdout.fileno(),
            stderr=stderr.fileno(),
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and tree.proc.poll() is None:
                assert time.monotonic() < deadline, "Command did not start"
                time.sleep(0.01)
            assert ready.read_text() == "isolated"
            assert tree.proc.poll() is None
        finally:
            tree.close()
        assert tree.proc.poll() is not None
        tree.close()
        stdout.seek(0)
        stderr.seek(0)
        assert stdout.read() == b"out\x00\xff"
        assert stderr.read() == b"err\x00\xfe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job API failures")
@pytest.mark.parametrize(
    "operation", ["TerminateJobObject", "QueryInformationJobObject"]
)
def test_job_cleanup_failure_closes_handle_and_reaps_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    tree = ProcessTree(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        os.environ.copy(),
    )
    if sys.platform == "win32":
        assert tree.job is not None
        close_handle = Mock(wraps=tree.job.kernel.CloseHandle)
        monkeypatch.setattr(tree.job.kernel, "CloseHandle", close_handle)
        monkeypatch.setattr(tree.job.kernel, operation, Mock(return_value=0))
        with pytest.raises(OSError):
            tree.close()
        close_handle.assert_called_once_with(tree.job.handle)
        assert tree.proc.poll() is not None
        assert tree.proc.stdin is not None and tree.proc.stdin.closed
        tree.close()
        close_handle.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job allocation")
def test_worker_launch_failure_closes_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "win32":
        job = process_ownership._Job()
        close = Mock(wraps=job.close)
        monkeypatch.setattr(job, "close", close)
        monkeypatch.setattr(process_ownership, "_Job", lambda: job)
        monkeypatch.setattr(
            subprocess, "Popen", Mock(side_effect=OSError("worker launch failed"))
        )
        with pytest.raises(OSError, match="worker launch failed"):
            ProcessTree([sys.executable], tmp_path, os.environ.copy())
        close.assert_called_once()


@pytest.mark.parametrize("shape", ["cli", "http"])
def test_factory_setup_runtime_and_teardown(tmp_path: Path, shape: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "setup.py").write_text(
        "import os; from pathlib import Path; "
        "Path(os.environ['FACTORY_RUNTIME_STATE'], 'setup').write_text('ready')"
    )
    (source / "run.py").write_text(
        """import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
state = Path(os.environ['FACTORY_RUNTIME_STATE'])
assert (state / 'setup').read_text() == 'ready'
(state / 'ran').touch()
if os.environ['SHAPE'] == 'http':
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(os.environ['FACTORY_RUNTIME_CANDIDATE'].encode())
    address = ('127.0.0.1', int(os.environ['FACTORY_RUNTIME_PORT']))
    HTTPServer(address, Handler).serve_forever()
"""
    )
    factory = Path(__file__).resolve().parents[1] / "factory"
    driver = """
import json, sys
from pathlib import Path
from urllib.error import HTTPError
sys.path.insert(0, sys.argv[1])
from runtime_host import RuntimeHost, request
root, shape = Path(sys.argv[2]), sys.argv[3]
config = root / 'runtime.json'
config.write_text(json.dumps({
    'version': 1,
    'roots': {'candidate': str(root / 'source')},
    'include': ['setup.py', 'run.py'], 'shape': shape,
    'setup': ['{python}', 'setup.py'], 'command': ['{python}', 'run.py'],
    'env': {'SHAPE': shape}, 'health_path': '/health', 'identity_path': '/identity',
    'timeout_s': 5,
}))
with RuntimeHost(config) as host:
    def call(action, **data):
        return request(action, data, host.connection)
    call('setup', slot='test')
    result = call('start', slot='test', root='candidate')
    state, snapshot = Path(result['state']), Path(result['snapshot'])
    assert (state / 'ran').exists()
    assert result['exit_code'] == (0 if shape == 'cli' else None)
    assert call('identity', slot='test')['candidate'] == result['candidate']
    assert call('teardown', slot='test')['stopped']
    assert not state.exists() and not snapshot.exists()
    assert call('teardown', slot='test')['stopped']
    final = call('start', slot='test', root='candidate')
    base = Path(final['state']).parent.parent
    for script in ('setup.py', 'run.py'):
        path = root / 'source' / script
        original = path.read_text()
        path.write_text('raise SystemExit(7)')
        try:
            failed = call('start', slot='test', root='candidate')
        except HTTPError as error:
            assert error.code == 400
            assert not list(base.iterdir())
        else:
            assert script == 'run.py' and shape == 'cli'
            assert failed['exit_code'] == 7
            call('teardown', slot='test')
        path.write_text(original)
    final = call('start', slot='test', root='candidate')
assert host.proc.poll() is not None
assert not Path(final['state']).exists() and not Path(final['snapshot']).exists()
"""
    result = subprocess.run(
        [sys.executable, "-c", driver, str(factory), str(tmp_path), shape],
        cwd=tmp_path,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
