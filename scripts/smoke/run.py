"""One smoke run: a fresh database and data directory, the app started, every feature's
checks, a restart on the same state, then a start without a database. Feature checks
come from `scripts/smoke/features/` in `f<NN>` order."""

import argparse
import importlib
import json
import os
import pkgutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType

from app.config import Settings
from app.db import connect
from scripts.smoke import features
from scripts.smoke.core import (
    ROOT,
    Context,
    inspect_page,
    ledger,
    running_app,
    wait_for_health,
)


def feature_modules() -> list[ModuleType]:
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(features.__path__)
        if info.name.startswith("f")
    )
    return [importlib.import_module(f"{features.__name__}.{name}") for name in names]


def verify(observations: dict[str, object]) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL must name a supplied fresh pgvector database")
    candidate = os.environ.get("FACTORY_RUNTIME_CANDIDATE")
    if candidate is None:
        raise ValueError("FACTORY_RUNTIME_CANDIDATE must identify this checkout")
    observations["candidate"] = candidate
    with connect(database_url) as connection:
        tables = connection.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        ).fetchall()
        assert not tables, (
            "Smoke requires a supplied fresh database with no user tables"
        )
    observations["fresh_database_user_tables"] = []
    modules = feature_modules()
    observations["features"] = [
        module.__name__.rsplit(".", 1)[-1] for module in modules
    ]
    with tempfile.TemporaryDirectory(prefix="tka-smoke-") as state:
        environment = os.environ.copy()
        environment.update(
            TKA_DATA_DIR=state, TKA_MODELS="fake", TKA_ALLOW_LOCAL_SOURCES="1"
        )
        environment.pop("OPENROUTER_API_KEY", None)
        observations["fresh_state"] = state
        settings = Settings(
            database_url, Path(state), models_mode="fake", allow_local_sources=True
        )
        with running_app(environment, observations, "first_start") as (process, client):
            health = wait_for_health(process, client, candidate, 200)
            observations["health"] = {
                "status": health.status_code,
                "body": health.json(),
            }
            observations["identity"] = {
                "status": 200,
                "body": client.get("/api/build-id").text,
            }
            observations["assets"] = inspect_page(client)
            assert client.get("/api/sources").json() == []
            ctx = Context(client, Path(state), database_url, settings, observations)
            for module in modules:
                before = getattr(module, "before_restart", None)
                if before:
                    before(ctx)
            sources = [item.model_dump(mode="json") for item in ctx.sources]
            assert client.get("/api/sources").json() == sources
            observations["sources_before_restart"] = sources
            if ctx.sources:
                observations["source_before_restart"] = sources[0]
            before_ledger = ledger(database_url)
            observations["ledger_before_restart"] = before_ledger
        with running_app(environment, observations, "restart") as (process, client):
            wait_for_health(process, client, candidate, 200)
            after_ledger = ledger(database_url)
            assert after_ledger == before_ledger
            observations["ledger_after_restart"] = after_ledger
            ctx.client = client
            if ctx.sources:
                persisted = client.get(f"/api/sources/{ctx.sources[0].id}")
                assert persisted.status_code == 200
                assert persisted.json() == sources[0]
                observations["source_after_restart"] = persisted.json()
            persisted_sources = client.get("/api/sources").json()
            assert persisted_sources == sources
            observations["sources_after_restart"] = persisted_sources
            for module in modules:
                after = getattr(module, "after_restart", None)
                if after:
                    after(ctx)
        environment.pop("DATABASE_URL", None)
        with running_app(environment, observations, "missing_database") as (
            process,
            client,
        ):
            health = wait_for_health(process, client, candidate, 503)
            observations["missing_database_health"] = {
                "status": 503,
                "body": health.json(),
            }
            inspect_page(client)
    observations["state_cleanup_confirmed"] = not Path(state).exists()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    observations: dict[str, object] = {"cwd": str(ROOT)}
    try:
        verify(observations)
    except Exception as error:
        observations["passed"] = False
        observations["error_type"] = type(error).__name__
        print(
            "Smoke failed. Requires DATABASE_URL for a fresh pgvector database, "
            "FACTORY_RUNTIME_CANDIDATE, and a successful web build.",
            file=sys.stderr,
        )
        result = 1
    else:
        observations["passed"] = True
        result = 0
    output = json.dumps(observations, indent=2)
    print(output)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(output + "\n", encoding="utf-8")
    return result
