from collections.abc import Callable

from fastapi import FastAPI

from app.config import Settings
from app.sources import source_router


def install(application: FastAPI, get_settings: Callable[[], Settings]) -> None:
    application.include_router(source_router(get_settings))
