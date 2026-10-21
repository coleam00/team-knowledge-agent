import os
import re
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from psycopg.rows import TupleRow, dict_row
from pydantic import BaseModel, NonNegativeInt, StrictStr

from app import db
from app.config import Settings
from app.git import git
from app.source_files import (
    SourceFile,
    SourceFileError,
    SourceFileErrorCode,
    fetch_source_file,
)

_FILE_ERROR_STATUS: dict[SourceFileErrorCode, int] = {
    "not_found": 404,
    "invalid_path": 422,
    "invalid_range": 422,
    "unavailable": 503,
    "busy": 503,
}


class SourceInput(BaseModel):
    url: StrictStr


class Source(BaseModel):
    id: UUID
    url: str
    owner: str
    name: str
    status: Literal["queued", "indexing", "ready", "failed"]
    indexed_commit: str | None
    indexed_at: datetime | None
    error: str | None
    file_count: NonNegativeInt
    chunk_count: NonNegativeInt
    created_at: datetime


@dataclass(frozen=True)
class SourceLocation:
    url: str
    owner: str
    name: str


def github_location(url: str) -> SourceLocation:
    match = re.fullmatch(
        r"https://github\.com/([a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?)/"
        r"([a-z0-9_.-]{1,104})/?",
        url,
        re.IGNORECASE | re.ASCII,
    )
    if match is None:
        raise ValueError(
            "Use a public GitHub HTTPS repository URL without credentials, "
            "query, fragment, or file paths"
        )
    owner, name = (part.lower() for part in match.groups())
    name = name.removesuffix(".git")
    if not name or name in {".", ".."} or name.endswith(".git") or len(name) > 100:
        raise ValueError("Invalid GitHub repository name")
    return SourceLocation(f"https://github.com/{owner}/{name}", owner, name)


def source_location(url: str, allow_local: bool) -> SourceLocation:
    if not url or any(ord(character) < 32 for character in url):
        raise ValueError(
            "Source must be a repository URL or an absolute local Git path"
        )
    path = Path(url)
    if not path.is_absolute():
        return github_location(url)
    if not allow_local:
        raise ValueError("Local sources require TKA_ALLOW_LOCAL_SOURCES=1")
    if url.startswith(("//", "\\\\")):
        raise ValueError("Network paths are not local Git sources")
    try:
        path = path.resolve(strict=True)
        bare = git(path, "rev-parse", "--is-bare-repository", timeout=5) == "true"
        root = git(
            path,
            "rev-parse",
            "--absolute-git-dir" if bare else "--show-toplevel",
            timeout=5,
        )
        if Path(root).resolve() != path:
            raise ValueError("Local source must be a Git repository root")
        origin = git(
            path,
            "config",
            "--local",
            "--no-includes",
            "--get",
            "remote.origin.url",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValueError(
            "Local source must be a Git repository with an origin"
        ) from error
    metadata = github_location(origin)
    return SourceLocation(os.path.normcase(str(path)), metadata.owner, metadata.name)


@contextmanager
def source_connection(
    database_url: str | None,
) -> Iterator[psycopg.Connection[TupleRow]]:
    try:
        with db.connect(database_url) as connection:
            yield connection
    except psycopg.Error:
        raise HTTPException(503, "Source storage is unavailable") from None


def source_router(get_settings: Callable[[], Settings]) -> APIRouter:
    router = APIRouter(prefix="/api/sources")

    @router.post("", status_code=202, response_model=Source)
    def register(
        body: SourceInput, settings: Annotated[Settings, Depends(get_settings)]
    ) -> Source | JSONResponse:
        try:
            location = source_location(body.url, settings.allow_local_sources)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        with source_connection(settings.database_url) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "INSERT INTO sources (id, url, owner, name) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (url) DO NOTHING RETURNING *",
                    (uuid4(), location.url, location.owner, location.name),
                )
                row = cursor.fetchone()
                if row is not None:
                    return Source.model_validate(row)
                # A separate statement sees the winning concurrent transaction.
                cursor.execute("SELECT id FROM sources WHERE url = %s", (location.url,))
                existing = cursor.fetchone()
                assert existing is not None
                return JSONResponse(
                    {"detail": "Source already exists", "id": str(existing["id"])},
                    status_code=409,
                )

    @router.get("")
    def list_sources(
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> list[Source]:
        with source_connection(settings.database_url) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM sources ORDER BY created_at, id")
                return [Source.model_validate(row) for row in cursor.fetchall()]

    @router.get("/{source_id}")
    def get_source(
        source_id: UUID, settings: Annotated[Settings, Depends(get_settings)]
    ) -> Source:
        with source_connection(settings.database_url) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM sources WHERE id = %s", (source_id,))
                row = cursor.fetchone()
                if row is None:
                    raise HTTPException(404, "Source not found")
                return Source.model_validate(row)

    @router.get("/{source_id}/file", response_model=SourceFile)
    def get_file(
        source_id: UUID,
        settings: Annotated[Settings, Depends(get_settings)],
        path: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> SourceFile:
        try:
            return fetch_source_file(
                source_id, path=path, start=start, end=end, settings=settings
            )
        except SourceFileError as error:
            status = _FILE_ERROR_STATUS[error.code]
            headers = {"Retry-After": "1"} if error.code == "busy" else None
            raise HTTPException(status, str(error), headers=headers) from None

    return router
