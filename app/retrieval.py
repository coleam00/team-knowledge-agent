import math
from fractions import Fraction
from urllib.parse import quote
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, PositiveInt

from app import db
from app.config import Settings
from app.index_store import SearchCandidate, hybrid_candidates
from app.models import Embedder, get_embedder


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: PositiveInt
    source_id: UUID
    repo: str
    path: str
    start_line: PositiveInt
    end_line: PositiveInt
    commit: str
    url: str
    snippet: str


class SearchUnavailable(RuntimeError):
    pass


class InvalidSearch(ValueError):
    pass


def rrf_score(fts_rank: int | None, vector_rank: int | None) -> Fraction:
    return sum(
        (
            Fraction(1, 60 + rank)
            for rank in (fts_rank, vector_rank)
            if rank is not None
        ),
        start=Fraction(),
    )


def citation(candidate: SearchCandidate, n: int) -> Citation:
    prefix = "/".join(
        quote(part, safe="")
        for part in (candidate.owner, candidate.name, "blob", candidate.indexed_commit)
    )
    url = (
        f"https://github.com/{prefix}/{quote(candidate.path, safe='/')}"
        f"#L{candidate.start_line}-L{candidate.end_line}"
    )
    return Citation(
        n=n,
        source_id=candidate.source_id,
        repo=f"{candidate.owner}/{candidate.name}",
        path=candidate.path,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        commit=candidate.indexed_commit,
        url=url,
        snippet=candidate.content,
    )


def search(
    settings: Settings,
    query: str,
    *,
    source_id: UUID | None = None,
    limit: int = 10,
    embedder: Embedder | None = None,
) -> list[Citation]:
    query = query.strip()
    if not query or type(limit) is not int or limit <= 0:
        raise InvalidSearch(
            "Search requires a nonblank query and a positive integer limit"
        )
    if source_id is not None and not isinstance(source_id, UUID):
        raise InvalidSearch("source_id must be a UUID")
    if not settings.database_url:
        raise SearchUnavailable("Search is unavailable")
    try:
        model = embedder if embedder is not None else get_embedder(settings)
        vector = model.embed(query)
    except RuntimeError:
        raise SearchUnavailable("Search is unavailable") from None
    if not vector or not all(math.isfinite(value) for value in vector):
        raise SearchUnavailable("Search is unavailable")
    try:
        with db.connect(settings.database_url) as connection:
            candidates = hybrid_candidates(
                connection,
                query,
                query_embedding=vector,
                embedding_model=model.model_id,
                min_similarity=settings.search_min_similarity,
                source_id=source_id,
            )
    except psycopg.Error:
        raise SearchUnavailable("Search is unavailable") from None
    candidates.sort(key=lambda c: (-rrf_score(c.fts_rank, c.vector_rank), c.id))
    return [citation(candidate, n) for n, candidate in enumerate(candidates[:limit], 1)]
