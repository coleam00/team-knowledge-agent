from unittest.mock import Mock

import psycopg
import pytest

from scripts import smoke


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
            smoke.ledger(database_url)
        else:
            smoke.verify({})

    connect.assert_called_once_with(
        database_url,
        connect_timeout=3,
        options=(
            "-c search_path=tka_custom,public "
            "-c statement_timeout=5000 -c lock_timeout=5000"
        ),
    )
