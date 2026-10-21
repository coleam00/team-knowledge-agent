import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.build_id import resolve_build_id
from app.config import ModelConfigurationError, Settings
from app.sources import source_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    try:
        settings = Settings.from_env()
    except ModelConfigurationError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        logger.error("Configuration failed (%s)", type(error).__name__)
        settings = None
    build_id = resolve_build_id()
    ready = False

    async def initialize() -> None:
        nonlocal ready
        if settings is None:
            return
        try:
            await asyncio.to_thread(
                settings.data_dir.mkdir, parents=True, exist_ok=True
            )
            await asyncio.to_thread(db.run_migrations, settings.database_url)
        except Exception as error:
            logger.error("Initialization failed (%s)", type(error).__name__)
        else:
            ready = True

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(initialize())
        try:
            yield
        finally:
            await task

    application = FastAPI(lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"type": item["type"], "loc": item["loc"]}
                    for item in error.errors()
                ]
            },
        )

    def source_settings() -> Settings:
        if not ready or settings is None:
            raise HTTPException(503, "Source storage is unavailable")
        return settings

    application.include_router(source_router(source_settings))

    @application.get("/api/health")
    def health() -> JSONResponse:
        if ready and settings is not None:
            try:
                db.probe(settings.database_url)
            except Exception as error:
                logger.warning("Database probe failed (%s)", type(error).__name__)
            else:
                return JSONResponse({"status": "ok", "db": "ok"})
        return JSONResponse(
            {"status": "unavailable", "db": "unavailable"}, status_code=503
        )

    @application.get("/api/build-id", response_class=PlainTextResponse)
    def identity() -> str:
        return build_id

    @application.get("/", response_model=None)
    def index() -> FileResponse | PlainTextResponse:
        path = STATIC_DIR / "index.html"
        if not path.is_file():
            return PlainTextResponse(
                "Web build required: run uv run python scripts/build_web.py",
                status_code=503,
            )
        return FileResponse(path)

    if (STATIC_DIR / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"))
    return application
