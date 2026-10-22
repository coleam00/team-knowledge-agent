import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self

import anyio
from fastapi import HTTPException

from app import db
from app.config import ModelConfigurationError, Settings

logger = logging.getLogger(__name__)


@dataclass
class AppRuntime:
    settings: Settings | None
    ready: bool = False

    @classmethod
    def from_env(cls) -> Self:
        try:
            return cls(Settings.from_env())
        except ModelConfigurationError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            logger.error("Configuration failed (%s)", type(error).__name__)
            return cls(None)

    def _initialize_storage(self) -> None:
        if self.settings is not None:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            db.run_migrations(self.settings.database_url)

    async def _initialize(self) -> None:
        if self.settings is None:
            return
        # A migration owns a connection until its worker returns, even on shutdown.
        with anyio.CancelScope(shield=True):
            try:
                await anyio.to_thread.run_sync(self._initialize_storage)
            except Exception as error:
                logger.error("Initialization failed (%s)", type(error).__name__)
            else:
                self.ready = True

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self._initialize)
                yield
        finally:
            self.ready = False

    def get_settings(self) -> Settings:
        if not self.ready or self.settings is None:
            raise HTTPException(503, "Storage is unavailable")
        return self.settings
