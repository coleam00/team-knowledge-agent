from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings
from app.retrieval import Citation, InvalidSearch, SearchUnavailable, search


def search_router(get_settings: Callable[[], Settings]) -> APIRouter:
    router = APIRouter(prefix="/api/search")

    @router.get("")
    def get_search(
        settings: Annotated[Settings, Depends(get_settings)],
        q: str,
        source_id: UUID | None = None,
        limit: Annotated[str, Query(pattern=r"^[0-9]+$")] = "10",
    ) -> list[Citation]:
        try:
            count = int(limit)
        except ValueError:
            raise HTTPException(422, "Invalid search parameters") from None
        try:
            return search(settings, q, source_id=source_id, limit=count)
        except InvalidSearch:
            raise HTTPException(422, "Invalid search parameters") from None
        except SearchUnavailable:
            raise HTTPException(503, "Search is unavailable") from None

    return router
