from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.ask import AskResult, AskUnavailable, ask
from app.config import Settings
from app.retrieval import InvalidSearch


class AskRequest(BaseModel):
    question: str
    source_ids: set[UUID] | None = None


def install(application: FastAPI, get_settings: Callable[[], Settings]) -> None:
    @application.post("/api/ask")
    def post_ask(
        body: AskRequest,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> AskResult:
        try:
            return ask(settings, body.question, source_ids=body.source_ids)
        except InvalidSearch:
            raise HTTPException(422, "Invalid ask parameters") from None
        except AskUnavailable:
            raise HTTPException(503, "Answers are unavailable") from None
