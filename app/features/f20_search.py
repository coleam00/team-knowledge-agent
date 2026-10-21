from collections.abc import Callable

from fastapi import FastAPI

from app.config import Settings
from app.search import search_router


def install(application: FastAPI, get_settings: Callable[[], Settings]) -> None:
    application.include_router(search_router(get_settings))
