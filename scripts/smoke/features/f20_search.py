"""Hybrid search: two persisted handbook sources searched with commit-pinned citations,
identical before and after a restart."""

from pathlib import Path
from uuid import uuid4

import httpx

from app.db import connect
from app.git import git
from app.index_store import ChunkInput, replace_file
from app.models.fake import FakeEmbedder
from app.retrieval import Citation
from app.sources import Source
from scripts.smoke.core import Context, register_handbook


def seed_search(database_url: str, source: Source) -> Source:
    repository = Path(source.url)
    commit = git(repository, "rev-parse", "HEAD")
    model = FakeEmbedder()
    paths = ["docs/deploy.md", "docs/legacy-deploy.md", "docs/onboarding.md"]
    with connect(database_url) as connection, connection.transaction():
        for path in paths:
            content = (repository / path).read_bytes().decode("utf-8")
            replace_file(
                connection,
                source_id=source.id,
                path=path,
                blob_sha=git(repository, "rev-parse", f"{commit}:{path}"),
                language="markdown",
                chunks=[
                    ChunkInput(
                        start_line=1,
                        end_line=len(content.splitlines()),
                        content=content,
                        embedding=tuple(model.embed(content)),
                        embedding_model=model.model_id,
                    )
                ],
            )
        connection.execute(
            "UPDATE sources SET indexed_commit = %s, file_count = %s, chunk_count = %s "
            "WHERE id = %s",
            (commit, len(paths), len(paths), source.id),
        )
    return source.model_copy(
        update={
            "indexed_commit": commit,
            "file_count": len(paths),
            "chunk_count": len(paths),
        }
    )


def inspect_search(client: httpx.Client, source: Source) -> list[dict[str, object]]:
    parameters = {"q": "deploy", "source_id": str(source.id), "limit": "2"}
    response = client.get("/api/search", params=parameters)
    assert response.status_code == 200
    citations = [Citation.model_validate(item) for item in response.json()]
    assert len(citations) == 2
    assert [c.n for c in citations] == [1, 2]
    assert "docs/deploy.md" in {c.path for c in citations}
    for citation in citations:
        assert citation.source_id == source.id
        assert citation.commit == source.indexed_commit
        assert citation.repo == "tka-fixtures/handbook"
        content = (Path(source.url) / citation.path).read_bytes().decode("utf-8")
        assert citation.snippet == "".join(
            content.splitlines(keepends=True)[
                citation.start_line - 1 : citation.end_line
            ]
        )
        assert citation.url == (
            f"https://github.com/tka-fixtures/handbook/blob/{source.indexed_commit}/"
            f"{citation.path}#L{citation.start_line}-L{citation.end_line}"
        )
    assert client.get("/api/search", params=parameters).json() == response.json()
    for params in [
        {"q": "galaxies watermelon"},
        {"q": "deploy", "source_id": str(uuid4())},
    ]:
        empty = client.get("/api/search", params=params)
        assert empty.status_code == 200 and empty.json() == []
    for params in [{"q": ""}, {"q": "deploy", "limit": "0"}]:
        assert client.get("/api/search", params=params).status_code == 422
    return [citation.model_dump(mode="json") for citation in citations]


def before_restart(ctx: Context) -> None:
    source = register_handbook(ctx.client, ctx.state)
    second = register_handbook(ctx.client, ctx.state)
    assert source.id != second.id
    source = seed_search(ctx.database_url, source)
    second = seed_search(ctx.database_url, second)
    search_before = inspect_search(ctx.client, source)
    ctx.observations["seeded_search_before_restart"] = search_before
    ctx.observations["search_setup"] = "Persisted fixture rows; not ingestion"
    ctx.sources.extend([source, second])
    ctx.data["search_source"] = source
    ctx.data["search_before"] = search_before


def after_restart(ctx: Context) -> None:
    source = ctx.data["search_source"]
    assert isinstance(source, Source)
    search_after = inspect_search(ctx.client, source)
    assert search_after == ctx.data["search_before"]
    ctx.observations["seeded_search_after_restart"] = search_after
