import math
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import TupleRow

from app import db
from app.config import Settings
from app.git import git
from app.index_store import (
    ChunkInput,
    full_text_search,
    hybrid_candidates,
    replace_file,
)
from app.models.fake import FakeEmbedder
from app.retrieval import SearchUnavailable, rrf_score, search
from tests.handbook import create_handbook_repo


@dataclass
class FixedEmbedder:
    vector: list[float]
    model_id: str = "active"

    def embed(self, text: str) -> list[float]:
        return self.vector


def seed_source(
    connection: psycopg.Connection[TupleRow], commit: str | None = "a" * 40
) -> UUID:
    source = uuid4()
    connection.execute(
        "INSERT INTO sources (id, url, owner, name, indexed_commit) "
        "VALUES (%s,%s,%s,%s,%s)",
        (source, f"https://github.com/acme/{source}", "acme", "handbook", commit),
    )
    return source


def seed_chunk(
    connection: psycopg.Connection[TupleRow],
    source: UUID,
    identity: int,
    content: str = "deploy",
    vector: tuple[float, ...] = (1.0, 0.0),
    model: str = "active",
    path: str | None = None,
    start: int = 1,
    end: int = 1,
) -> None:
    file_id = replace_file(
        connection,
        source_id=source,
        path=path or f"{identity}.md",
        blob_sha="blob",
        language="markdown",
        chunks=[
            ChunkInput(
                start_line=start,
                end_line=end,
                content=content,
                embedding=vector,
                embedding_model=model,
            )
        ],
    )
    connection.execute(
        "UPDATE chunks SET id = %s WHERE file_id = %s", (UUID(int=identity), file_id)
    )


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    db.run_migrations(database_url)
    return Settings(database_url, tmp_path, models_mode="fake")


def test_rrf_arithmetic() -> None:
    assert rrf_score(1, 2) == pytest.approx(1 / 61 + 1 / 62)
    assert rrf_score(None, 1) == pytest.approx(1 / 61)


@pytest.mark.database
@pytest.mark.parametrize(
    "low,high",
    [
        ((12, 28), (6, 39)),
        ((28, 12), (6, 39)),
        ((12, 28), (39, 6)),
        ((28, 12), (39, 6)),
        ((30, 50), (39, 39)),
        ((50, 30), (39, 39)),
    ],
)
def test_exact_rrf_ties_use_uuid_before_limit(
    settings: Settings, low: tuple[int, int], high: tuple[int, int]
) -> None:
    ranks = {1: low, 2: high}
    lexical = [r for r in range(1, 51) if r not in (low[0], high[0])]
    semantic = [r for r in range(1, 51) if r not in (low[1], high[1])]
    ranks.update(enumerate(zip(lexical, semantic, strict=True), 3))
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        for identity, (fts_rank, vector_rank) in ranks.items():
            seed_chunk(
                connection,
                source,
                identity,
                content="deploy " * (51 - fts_rank),
                vector=(1, vector_rank / 50),
            )
        candidates = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[1, 0],
            embedding_model="active",
            min_similarity=0.25,
        )
        assert {c.id.int: (c.fts_rank, c.vector_rank) for c in candidates} == ranks
    expected = sorted(
        ranks, key=lambda i: (-sum(Fraction(1, 60 + r) for r in ranks[i]), i)
    )
    results = search(settings, "deploy", embedder=FixedEmbedder([1, 0]), limit=50)
    assert [c.path for c in results] == [f"{i}.md" for i in expected]
    boundary = expected.index(1) + 1
    assert expected[boundary] == 2
    limited = search(settings, "deploy", embedder=FixedEmbedder([1, 0]), limit=boundary)
    assert limited == results[:boundary]
    assert limited[-1].path == "1.md"


@pytest.mark.database
@pytest.mark.parametrize("mode", ["fake", "real"])
def test_configured_similarity_changes_search_results(
    settings: Settings, mode: str
) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        seed_chunk(connection, source, 1, content="semantic", vector=(0.6, 0.8))
        seed_chunk(
            connection, source, 2, content="semantic", vector=(0.3, math.sqrt(0.91))
        )
    model = FixedEmbedder([1, 0])
    configured = replace(settings, models_mode=mode)
    defaults = search(configured, "deploy", embedder=model)
    assert [c.path for c in defaults] == (
        ["1.md", "2.md"] if mode == "fake" else ["1.md"]
    )
    included = search(replace(configured, min_similarity=0.5), "deploy", embedder=model)
    assert [c.path for c in included] == ["1.md"]
    assert (
        search(replace(configured, min_similarity=0.8), "deploy", embedder=model) == []
    )


@pytest.mark.database
def test_overlap_is_deduplicated_and_fused_before_limit(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        seed_chunk(connection, source, 1, vector=(0, 1))
        seed_chunk(connection, source, 2)
        seed_chunk(connection, source, 3, content="semantic")
    results = search(settings, "deploy", embedder=FixedEmbedder([1, 0]), limit=3)
    assert [c.path for c in results] == ["2.md", "1.md", "3.md"]
    assert len({c.path for c in results}) == 3
    assert (
        search(settings, "deploy", embedder=FixedEmbedder([1, 0]), limit=1)
        == results[:1]
    )


@pytest.mark.database
def test_float32_underflow_query_keeps_lexical_results(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        seed_chunk(connection, seed_source(connection), 1)
    assert len(search(settings, "deploy", embedder=FixedEmbedder([1e-50, 0]))) == 1
    assert search(settings, "absent", embedder=FixedEmbedder([1e-50, 0])) == []


@pytest.mark.database
def test_branch_bounds_filtering_and_deterministic_fusion(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        other = seed_source(connection)
        invalid = seed_source(connection, "main")
        for identity in range(1, 62):
            seed_chunk(connection, invalid, identity)
            seed_chunk(connection, other, identity + 100)
            seed_chunk(connection, source, identity + 200, vector=(0.0, 1.0))
            seed_chunk(connection, source, identity + 300, content="semantic")
        candidates = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[1, 0],
            embedding_model="active",
            min_similarity=0.25,
            source_id=source,
        )
        lexical = sorted((c.fts_rank, c.id.int) for c in candidates if c.fts_rank)
        semantic = sorted(
            (c.vector_rank, c.id.int) for c in candidates if c.vector_rank
        )
        assert lexical == [(n, 200 + n) for n in range(1, 51)]
        assert semantic == [(n, 300 + n) for n in range(1, 51)]
        assert [
            c.id.int for c in full_text_search(connection, "deploy", source_id=source)
        ] == [i for _, i in lexical]
        unfiltered = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[1, 0],
            embedding_model="active",
            min_similarity=0.25,
        )
        assert {c.id.int for c in unfiltered} == set(range(101, 151))
        assert all(c.fts_rank == c.vector_rank for c in unfiltered)
    results = search(
        settings, "deploy", embedder=FixedEmbedder([1, 0]), source_id=source, limit=200
    )
    assert len(results) == 100
    assert [c.path for c in results[:4]] == ["201.md", "301.md", "202.md", "302.md"]
    assert [c.n for c in results] == list(range(1, 101))
    assert results == search(
        settings, "deploy", embedder=FixedEmbedder([1, 0]), source_id=source, limit=200
    )


@pytest.mark.database
def test_compatibility_threshold_and_lexical_independence(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        rows = [
            (1, "deploy", (1.0, 0.0), "active"),
            (2, "related", (0.0, 1.0), "active"),
            (3, "deploy", (1.0, 0.0), "foreign"),
            (4, "deploy", (1.0,), "foreign"),
            (5, "deploy", (1.0,), "active"),
            (6, "deploy", (0.0, 0.0), "active"),
            (7, "neither", (-1.0, 0.0), "active"),
        ]
        for identity, content, vector, model in rows:
            seed_chunk(connection, source, identity, content, vector, model)
        candidates = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[1, 0],
            embedding_model="active",
            min_similarity=0,
        )
        ranks = {c.id.int: (c.fts_rank, c.vector_rank) for c in candidates}
        assert ranks == {
            1: (1, 1),
            2: (None, 2),
            3: (2, None),
            4: (3, None),
            5: (4, None),
            6: (5, None),
        }
        strict = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[0, 1],
            embedding_model="active",
            min_similarity=1,
        )
        assert {c.id.int for c in strict} == {1, 2, 3, 4, 5, 6}
        zero = hybrid_candidates(
            connection,
            "deploy",
            query_embedding=[0, 0],
            embedding_model="active",
            min_similarity=-1,
        )
        assert {c.id.int for c in zero} == {1, 3, 4, 5, 6}
        assert all(c.vector_rank is None for c in zero)
    assert search(settings, "absent", embedder=FixedEmbedder([0, 0])) == []
    assert search(settings, "!!!") == []


@pytest.mark.database
def test_commit_eligibility(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        for n, commit in enumerate(
            [None, "", " ", "main", "a" * 39, "g" * 40, "a" * 40, "B" * 64], 1
        ):
            seed_chunk(connection, seed_source(connection, commit), n)
        seed_chunk(connection, uuid4(), 9)
    results = search(settings, "deploy", embedder=FixedEmbedder([1, 0]))
    assert [c.commit for c in results] == ["a" * 40, "B" * 64]


@pytest.mark.database
def test_exact_committed_lines_encoding_and_fake_identity(
    settings: Settings, tmp_path: Path
) -> None:
    repository = create_handbook_repo(tmp_path)
    path = "docs/deploy caf\u00e9.md"
    text = "".join(f"  deploy line {n}\n" for n in range(1, 21))
    (repository / path).write_bytes(text.encode("utf-8"))
    git(repository, "add", "--", path)
    git(repository, "commit", "-m", "Citation fixture")
    commit = git(repository, "rev-parse", "HEAD")
    committed = git(repository, "show", f"{commit}:{path}") + "\n"
    snippet = "".join(committed.splitlines(keepends=True)[13:18])
    model = FakeEmbedder()
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection, commit)
        seed_chunk(
            connection,
            source,
            1,
            snippet,
            tuple(model.embed(snippet)),
            model.model_id,
            path,
            14,
            18,
        )
    (repository / path).write_text("working tree differs", encoding="utf-8")
    results = search(settings, "  deploy  ")
    assert [c.model_dump(mode="json") for c in results] == [
        {
            "n": 1,
            "source_id": str(source),
            "repo": "acme/handbook",
            "path": path,
            "start_line": 14,
            "end_line": 18,
            "commit": commit,
            "url": f"https://github.com/acme/handbook/blob/{commit}/docs/deploy%20caf%C3%A9.md#L14-L18",
            "snippet": snippet,
        }
    ]
    assert results == search(settings, "deploy")
    assert search(settings, "galaxies watermelon") == []
    assert search(settings, "deploy", source_id=uuid4()) == []
    with db.connect(settings.database_url) as connection:
        candidates = hybrid_candidates(
            connection,
            "notlexical",
            query_embedding=model.embed(snippet),
            embedding_model=model.model_id,
            min_similarity=0.99,
        )
        assert len(candidates) == 1 and candidates[0].vector_rank == 1


@pytest.mark.database
def test_reserved_path_characters(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        seed_chunk(connection, source, 1, path="docs/100% #?.md")
    result = search(settings, "deploy", embedder=FixedEmbedder([1, 0]))[0]
    assert result.url.endswith("/docs/100%25%20%23%3F.md#L1-L1")


@pytest.mark.database
def test_atomic_publication_and_deletion(settings: Settings) -> None:
    with db.connect(settings.database_url) as writer:
        source = seed_source(writer)
        seed_chunk(writer, source, 1, "deploy old")
        seed_chunk(writer, source, 2, "legacy", vector=(0, 1))
    model = FixedEmbedder([0, 0])
    with db.connect(settings.database_url) as writer, writer.transaction():
        seed_chunk(writer, source, 3, "deploy new", path="1.md")
        writer.execute("DELETE FROM chunks WHERE path = '2.md'")
        writer.execute("DELETE FROM files WHERE path = '2.md'")
        writer.execute(
            "UPDATE sources SET indexed_commit = %s WHERE id = %s", ("b" * 40, source)
        )
        before = search(settings, "deploy", embedder=model)
        assert [(c.commit, c.snippet) for c in before] == [("a" * 40, "deploy old")]
        assert len(search(settings, "legacy", embedder=model)) == 1
    after = search(settings, "deploy", embedder=model)
    assert [(c.commit, c.snippet) for c in after] == [("b" * 40, "deploy new")]
    assert search(settings, "legacy", embedder=model) == []


@pytest.mark.parametrize("vector", [[], [float("nan")], [float("inf")]])
def test_unusable_embedding_fails_before_database(
    tmp_path: Path, vector: list[float]
) -> None:
    with pytest.raises(SearchUnavailable, match="Search is unavailable"):
        search(Settings("unused", tmp_path), "deploy", embedder=FixedEmbedder(vector))


@pytest.mark.parametrize(
    "query,limit",
    [("", 1), (" \t", 1), ("deploy", 0), ("deploy", -1), ("deploy", True)],
)
def test_direct_invalid_input(tmp_path: Path, query: str, limit: int) -> None:
    with pytest.raises(ValueError):
        search(Settings(None, tmp_path), query, limit=limit)


@pytest.mark.database
@pytest.mark.parametrize("mode,threshold", [("fake", 0.25), ("real", 0.35)])
def test_similarity_boundary_is_inclusive(
    settings: Settings, mode: str, threshold: float
) -> None:
    exact = (1.0,) * 16 if mode == "fake" else (7.0, 18.0, 5.0, 1.0, 1.0)
    dimensions = len(exact)
    query = [1.0] + [0.0] * (dimensions - 1)
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        for identity, score in [(1, threshold - 0.001), (3, threshold + 0.001)]:
            vector = (score, math.sqrt(1 - score * score)) + (0.0,) * (dimensions - 2)
            seed_chunk(connection, source, identity, "semantic", vector)
        seed_chunk(connection, source, 2, "semantic", exact)
        seed_chunk(connection, source, 4, "deploy", tuple([0.0] * dimensions))
    results = search(
        replace(settings, models_mode=mode), "deploy", embedder=FixedEmbedder(query)
    )
    assert {c.path for c in results} == {"2.md", "3.md", "4.md"}


def test_empty_source_selection_does_not_need_models_or_database(
    tmp_path: Path,
) -> None:
    assert search(Settings(None, tmp_path), "deploy", source_ids=set()) == []
    with pytest.raises(ValueError, match="not both"):
        search(Settings(None, tmp_path), "deploy", source_id=uuid4(), source_ids=set())


@pytest.mark.database
def test_old_fake_vectors_keep_only_lexical_eligibility(settings: Settings) -> None:
    with db.connect(settings.database_url) as connection:
        source = seed_source(connection)
        seed_chunk(
            connection, source, 1, "deploy", (1.0,) * 256, "fake-bow-sha256-256-v1"
        )
        seed_chunk(
            connection, source, 2, "unrelated", (1.0,) * 256, "fake-bow-sha256-256-v1"
        )
    assert [c.path for c in search(settings, "deploy")] == ["1.md"]
    assert search(settings, "freeze") == []
