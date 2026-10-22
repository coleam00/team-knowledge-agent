"""Own the ingest worker for one application lifespan."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import HTTPException

from app.config import Settings
from app.ingest.worker import run_worker


@asynccontextmanager
async def lifespan(get_settings: Callable[[], Settings]) -> AsyncIterator[None]:
    stop = Event()
    worker: Thread | None = None

    async def start() -> None:
        nonlocal worker
        while not stop.is_set():
            try:
                settings = get_settings()
            except HTTPException:
                await asyncio.sleep(0.05)
                continue
            settings.validate_ingest()
            started = Thread(target=run_worker, args=(settings, stop), name="ingest")
            try:
                started.start()
            except RuntimeError as error:
                logging.getLogger(__name__).error(
                    "Worker startup failed (%s)", type(error).__name__
                )
                await asyncio.sleep(0.1)
                continue
            worker = started
            return

    startup = asyncio.create_task(start())
    try:
        yield
    finally:
        stop.set()
        await startup
        if worker is not None:
            await asyncio.to_thread(worker.join, timeout=600)
            if worker.is_alive():
                raise RuntimeError("Ingest worker did not stop within 600 seconds")
