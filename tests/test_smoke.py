from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock

import httpx
import psycopg
import pytest

from scripts.smoke import core, run
from scripts.smoke.features import f90_unbuilt


def test_absent_features_exclude_delivered_search() -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404)

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://test"
    ) as client:
        observations = f90_unbuilt.absent_features(client)
    assert paths == ["/api/sources/1/reindex", "/api/ask"]
    assert all(item["stage"] == "not yet built" for item in observations)


@pytest.mark.parametrize("operation", ["ledger", "preflight"])
def test_smoke_preserves_database_schema_and_bounds_waits(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    database_url = (
        "postgresql://localhost/tka?options=-c%20search_path%3Dtka_custom%2Cpublic"
    )
    connect = Mock(side_effect=RuntimeError("connection intercepted"))
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FACTORY_RUNTIME_CANDIDATE", "test-candidate")

    with pytest.raises(RuntimeError, match="connection intercepted"):
        if operation == "ledger":
            core.ledger(database_url)
        else:
            run.verify({})

    connect.assert_called_once_with(
        database_url,
        connect_timeout=3,
        options=(
            "-c search_path=tka_custom,public "
            "-c statement_timeout=5000 -c lock_timeout=5000"
        ),
    )


@pytest.mark.parametrize("versions", [[1, 2], [1], [1, 2, 3]])
def test_ledger_matches_discovered_migrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, versions: list[int]
) -> None:
    for name in ["0001_initial.sql", "0002_next.sql"]:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(core, "MIGRATIONS", tmp_path)
    applied_at = datetime(2026, 1, 1, tzinfo=UTC)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("vector",)
    connection.execute.return_value.fetchall.return_value = [
        (version, applied_at) for version in versions
    ]
    monkeypatch.setattr(core, "connect", lambda _: connection)
    if versions == [1, 2]:
        assert core.ledger("test") == [
            {"version": version, "applied_at": applied_at.isoformat()}
            for version in versions
        ]
    else:
        with pytest.raises(AssertionError):
            core.ledger("test")
