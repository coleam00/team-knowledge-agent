from collections.abc import Iterator
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db, main, retrieval
from app.config import Settings
from app.models.fake import FakeEmbedder
from app.retrieval import search
from tests.test_http import ClientFactory, wait_for_health
from tests.test_retrieval import seed_chunk, seed_source

pytestmark = pytest.mark.database


@pytest.fixture
def search_client(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        yield client


def test_http_matches_shared_service_filter_limit_and_restart(
    search_client: TestClient,
    database_url: str,
    client_factory: ClientFactory,
) -> None:
    model = FakeEmbedder()
    with db.connect(database_url) as connection:
        first, second = seed_source(connection), seed_source(connection)
        for n in range(1, 13):
            seed_chunk(
                connection,
                first if n <= 3 else second,
                n,
                "deploy",
                tuple(model.embed("deploy")),
                model.model_id,
            )
    params = {"q": "deploy", "source_id": str(first), "limit": "2"}
    response = search_client.get("/api/search", params=params)
    assert response.status_code == 200
    expected = [
        c.model_dump(mode="json")
        for c in search(Settings.from_env(), "deploy", source_id=first, limit=2)
    ]
    assert response.json() == expected
    assert [c["n"] for c in expected] == [1, 2]
    assert {c["source_id"] for c in expected} == {str(first)}
    assert len(search_client.get("/api/search", params={"q": "deploy"}).json()) == 10
    assert (
        search_client.get(
            "/api/search",
            params={"q": "deploy", "limit": "02", "source_id": str(first)},
        ).json()
        == expected
    )
    assert (
        search_client.get(
            "/api/search", params={"q": "deploy", "source_id": str(uuid4())}
        ).json()
        == []
    )
    assert search_client.get("/api/search?q=watermelon").json() == []
    assert search_client.get("/api/search", params=params).json() == expected
    with client_factory(main.create_app()) as restarted:
        wait_for_health(restarted, 200)
        assert restarted.get("/api/search", params=params).json() == expected


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": " \t"},
        {"q": "deploy", "source_id": "bad-secret"},
        *[
            {"q": "deploy", "limit": limit}
            for limit in ["0", "-1", "2.0", "1e2", "", "a", "+2", " 2", "2\n"]
        ],
    ],
)
def test_invalid_query_parameters(
    search_client: TestClient, params: dict[str, str]
) -> None:
    response = search_client.get("/api/search", params=params)
    assert response.status_code == 422
    assert "bad-secret" not in response.text


@pytest.mark.parametrize("failure", ["database", "model", "empty-vector"])
def test_failures_are_sanitized_503(
    search_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    if failure == "database":
        monkeypatch.setattr(
            db,
            "connect",
            Mock(side_effect=psycopg.OperationalError("credential-sentinel")),
        )
    elif failure == "model":
        monkeypatch.setattr(
            retrieval,
            "get_embedder",
            Mock(side_effect=RuntimeError("credential-sentinel")),
        )
    else:
        monkeypatch.setattr(FakeEmbedder, "embed", lambda self, text: [])
    response = search_client.get("/api/search?q=deploy")
    assert response.status_code == 503
    assert response.json() == {"detail": "Search is unavailable"}
    assert "credential-sentinel" not in response.text + caplog.text
