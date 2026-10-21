import asyncio
import threading
from typing import Literal

import anyio
import pytest
from fastapi import HTTPException

from app import db
from app.runtime import AppRuntime
from tests.conftest import local_event_loop


@pytest.mark.parametrize("exit_kind", ["normal", "error", "cancel"])
def test_runtime_joins_initialization(
    monkeypatch: pytest.MonkeyPatch, exit_kind: Literal["normal", "error", "cancel"]
) -> None:
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def migrate(_: str | None) -> None:
        entered.set()
        try:
            assert release.wait(10)
        finally:
            closed.set()

    monkeypatch.setattr(db, "run_migrations", migrate)
    runtime = AppRuntime.from_env()

    async def check() -> None:
        exiting, finished = anyio.Event(), anyio.Event()

        async def owner() -> None:
            try:
                with anyio.CancelScope() as scope:
                    async with runtime.run():
                        assert await anyio.to_thread.run_sync(entered.wait, 5)
                        with pytest.raises(HTTPException):
                            runtime.get_settings()
                        exiting.set()
                        if exit_kind == "error":
                            raise ValueError("feature startup failed")
                        if exit_kind == "cancel":
                            scope.cancel()
            except ExceptionGroup as errors:
                assert exit_kind == "error"
                assert isinstance(errors.exceptions[0], ValueError)
            finally:
                finished.set()

        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(owner)
                await exiting.wait()
                try:
                    with anyio.move_on_after(0.05) as timeout:
                        await finished.wait()
                    assert timeout.cancelled_caught
                    assert not closed.is_set()
                finally:
                    release.set()
                await finished.wait()
            assert closed.is_set()
            assert not runtime.ready

    try:
        with asyncio.Runner(loop_factory=local_event_loop) as runner:
            runner.run(check())
    finally:
        release.set()
