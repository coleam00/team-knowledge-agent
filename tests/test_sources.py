import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import get_args
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError

from app import db, jobs, main
from app.config import Settings
from app.git import git
from app.sources import Source, source_location
from tests.handbook import ORIGIN, create_handbook_repo
from tests.test_http import ClientFactory, wait_for_health

pytestmark = pytest.mark.database


@pytest.fixture
def sources_client(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    client_factory: ClientFactory,
) -> Iterator[TestClient]:
    monkeypatch.setattr(main, "run_worker", lambda settings, stop: None)
    monkeypatch.setenv("DATABASE_URL", database_url)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        yield client


def test_registration_list_detail_and_restart(
    sources_client: TestClient, client_factory: ClientFactory
) -> None:
    assert sources_client.get("/api/sources").json() == []
    response = sources_client.post(
        "/api/sources", json={"url": "https://github.com/acme/handbook"}
    )
    assert response.status_code == 202
    source = response.json()
    assert source == {
        "id": source["id"],
        "url": "https://github.com/acme/handbook",
        "owner": "acme",
        "name": "handbook",
        "status": "queued",
        "indexed_commit": None,
        "indexed_at": None,
        "error": None,
        "file_count": 0,
        "chunk_count": 0,
        "created_at": source["created_at"],
    }
    assert source["id"] and source["created_at"]
    second_response = sources_client.post(
        "/api/sources", json={"url": "https://github.com/acme/runbooks"}
    )
    assert second_response.status_code == 202
    second = second_response.json()
    assert second["id"] != source["id"]
    assert sources_client.get("/api/sources").json() == [source, second]
    with client_factory(main.create_app()) as restarted:
        wait_for_health(restarted, 200)
        assert restarted.get("/api/sources").json() == [source, second]
        for registered in (source, second):
            assert (
                restarted.get(f"/api/sources/{registered['id']}").json() == registered
            )
        assert restarted.get(f"/api/sources/{uuid4()}").status_code == 404


def test_equivalent_urls_converge_concurrently(
    sources_client: TestClient, database_url: str
) -> None:
    urls = [
        "https://github.com/acme/handbook",
        "https://github.com/acme/handbook.git",
        "https://github.com/acme/handbook/",
        "https://GITHUB.com/Acme/Handbook.git/",
    ]
    barrier = Barrier(len(urls))

    def register(url: str) -> tuple[int, str]:
        barrier.wait(timeout=5)
        response = sources_client.post("/api/sources", json={"url": url})
        return response.status_code, response.json()["id"]

    with ThreadPoolExecutor(max_workers=len(urls)) as workers:
        results = list(workers.map(register, urls))
    assert sorted(status for status, _ in results) == [202, 409, 409, 409]
    assert len({source_id for _, source_id in results}) == 1
    assert len(sources_client.get("/api/sources").json()) == 1
    duplicate = sources_client.post("/api/sources", json={"url": urls[1]})
    assert duplicate.status_code == 409
    assert duplicate.json()["id"] == results[0][1]
    with db.connect(database_url) as connection:
        assert connection.execute(
            "SELECT status, started_at, finished_at, error FROM jobs"
        ).fetchall() == [("queued", None, None, None)]


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/acme/handbook",
        "http://github.com/acme/handbook",
        "git@github.com:acme/handbook.git",
        "ssh://git@github.com/acme/handbook",
        "https://user:credential-sentinel@github.com/acme/handbook",
        "https://github.com/acme/handbook/blob/main/README.md",
        "https://github.com/acme/handbook/tree/main",
        "https://github.com/acme/handbook/README.md",
        "https://github.com/acme/handbook?token=credential-sentinel",
        "https://github.com/acme/handbook#readme",
        "https://github.com:443/acme/handbook",
        "https://github.com.evil.test/acme/handbook",
        "https://github.com/acme/%68andbook",
        "https://github.com/acme/..",
        "https://github.com/acme/.git",
        "https://github.com/acme/handbook.git.git",
        "https://github.com/acme/handboo\u212a",
        "https://github.com/acme/handbook//",
        "https://github.com/acme/handbook\n",
        "https://github.com/acme",
        "https://[bad",
        "relative/path",
        "",
        None,
        42,
    ],
)
def test_invalid_sources_create_no_rows(
    sources_client: TestClient, database_url: str, url: object
) -> None:
    response = sources_client.post("/api/sources", json={"url": url})
    assert response.status_code == 422
    assert response.json()["detail"]
    assert "credential-sentinel" not in response.text
    assert sources_client.get("/api/sources").json() == []
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


def test_job_insert_failure_rolls_back_registration(
    sources_client: TestClient, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise psycopg.OperationalError("credential-sentinel")

    monkeypatch.setattr(jobs, "enqueue", fail)
    response = sources_client.post("/api/sources", json={"url": ORIGIN})
    assert response.status_code == 503
    with db.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


@pytest.mark.parametrize(
    "body",
    [
        '{"url": ["https://user:credential-sentinel@github.com/acme/repo"]}',
        '{"url": {"password": "credential-sentinel"}}',
        '"https://user:credential-sentinel@github.com/acme/repo"',
        '["credential-sentinel"]',
        '{"password": "credential-sentinel"}',
        '{"url": "credential-sentinel",}',
    ],
)
def test_body_validation_does_not_reflect_credentials(
    sources_client: TestClient, body: str
) -> None:
    response = sources_client.post(
        "/api/sources", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]
    assert "credential-sentinel" not in response.text
    assert all(set(error) == {"type", "loc"} for error in response.json()["detail"])
    assert sources_client.get("/api/sources").json() == []


def test_detail_validation_does_not_reflect_credentials(
    sources_client: TestClient,
) -> None:
    response = sources_client.get("/api/sources/user:credential-sentinel@github.com")
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"type": "uuid_parsing", "loc": ["path", "source_id"]}
    ]
    assert "credential-sentinel" not in response.text
    assert sources_client.get("/api/sources").json() == []


@pytest.mark.parametrize("suffix", ["\u00a0", "\u2003"])
@pytest.mark.parametrize("bare", [False, True], ids=["worktree", "bare"])
def test_local_roots_preserve_trailing_whitespace(
    sources_client: TestClient,
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
    bare: bool,
) -> None:
    repository = tmp_path / f"repository{suffix}"
    repository.mkdir()
    arguments = ["init", "--template="]
    if bare:
        arguments.append("--bare")
    git(repository, *arguments)
    git(repository, "remote", "add", "origin", ORIGIN)
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", "1")
    with client_factory(main.create_app()) as enabled:
        wait_for_health(enabled, 200)
        response = enabled.post("/api/sources", json={"url": str(repository)})
        assert response.status_code == 202
        source = response.json()
        assert Path(source["url"]).samefile(repository)
        assert (source["owner"], source["name"]) == ("tka-fixtures", "handbook")
        assert source["status"] == "queued"


def test_local_opt_in_metadata_and_root_validation(
    sources_client: TestClient,
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = create_handbook_repo(tmp_path)
    body = {"url": str(repository)}
    assert sources_client.post("/api/sources", json=body).status_code == 422
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", "1")
    with client_factory(main.create_app()) as enabled:
        wait_for_health(enabled, 200)
        response = enabled.post("/api/sources", json=body)
        assert response.status_code == 202
        source = response.json()
        assert (source["owner"], source["name"]) == ("tka-fixtures", "handbook")
        assert Path(source["url"]).samefile(repository)
        assert source["status"] == "queued"
        duplicate = enabled.post(
            "/api/sources", json={"url": str(repository / "docs" / "..")}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["id"] == source["id"]
        for invalid in [tmp_path, repository / "docs", repository / "missing"]:
            assert (
                enabled.post("/api/sources", json={"url": str(invalid)}).status_code
                == 422
            )
        assert (
            enabled.post("/api/sources", json={"url": "relative/path"}).status_code
            == 422
        )
        git(repository, "remote", "set-url", "origin", "git@github.com:acme/private")
        assert enabled.post("/api/sources", json=body).status_code == 422
        assert enabled.get("/api/sources").json() == [source]


def test_database_enforces_url_uniqueness(
    sources_client: TestClient, database_url: str
) -> None:
    assert sources_client.post("/api/sources", json={"url": ORIGIN}).status_code == 202
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        db.connect(database_url) as conn,
    ):
        conn.execute(
            "INSERT INTO sources (id, url, owner, name) VALUES (%s, %s, %s, %s)",
            (uuid4(), ORIGIN, "tka-fixtures", "handbook"),
        )


def test_database_status_domain_matches_response_model(
    sources_client: TestClient, database_url: str
) -> None:
    statuses = get_args(Source.model_fields["status"].annotation)
    assert statuses
    with db.connect(database_url) as conn:
        conn.execute(
            sql.SQL(
                "CREATE TEMP TABLE expected_source_status "
                "(status text CHECK (status IN ({})))"
            ).format(sql.SQL(", ").join(sql.Literal(status) for status in statuses))
        )
        actual = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'sources'::regclass "
            "AND conname = 'sources_status_check'"
        ).fetchone()
        expected = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'expected_source_status'::regclass AND contype = 'c'"
        ).fetchone()
        assert actual is not None and actual == expected

    for status in statuses:
        response = sources_client.post(
            "/api/sources", json={"url": f"https://github.com/acme/{status}"}
        )
        assert response.status_code == 202
        source = response.json()
        with db.connect(database_url) as conn:
            conn.execute(
                "UPDATE sources SET status = %s WHERE id = %s",
                (status, source["id"]),
            )
        source["status"] = status
        assert sources_client.get(f"/api/sources/{source['id']}").json() == source
    assert [
        source["status"] for source in sources_client.get("/api/sources").json()
    ] == (list(statuses))
    for invalid in ("", "unknown", "READY"):
        with pytest.raises(ValidationError):
            Source.model_validate({**source, "status": invalid})
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            db.connect(database_url) as conn,
        ):
            conn.execute(
                "UPDATE sources SET status = %s WHERE id = %s",
                (invalid, source["id"]),
            )


def test_storage_failure_is_secret_safe(
    sources_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_: str | None) -> None:
        raise psycopg.OperationalError("credential-sentinel")

    monkeypatch.setattr(db, "connect", fail)
    for method, path, body in [
        ("GET", "/api/sources", None),
        ("GET", f"/api/sources/{uuid4()}", None),
        ("POST", "/api/sources", {"url": ORIGIN}),
    ]:
        response = sources_client.request(method, path, json=body)
        assert response.status_code == 503
        assert "credential-sentinel" not in response.text


def test_local_git_ignores_ambient_configuration_and_never_executes_repo_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = create_handbook_repo(tmp_path)
    other = create_handbook_repo(tmp_path)
    git(other, "remote", "set-url", "origin", "https://github.com/wrong/repo")
    sentinel = tmp_path / "executed"
    command = f'echo executed > "{sentinel.as_posix()}"'
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\n{command}\n")
    hook.chmod(0o755)
    git(repository, "config", "core.hooksPath", str(hook.parent))
    git(repository, "config", "core.fsmonitor", command)
    git(repository, "config", "core.sshCommand", command)
    git(repository, "config", "credential.helper", f"!{command}")
    included = tmp_path / "included-config"
    included.write_text('[remote "origin"]\n url = https://github.com/wrong/repo\n')
    git(repository, "config", "include.path", str(included))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "remote.origin.url")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/wrong/repo")
    location = source_location(str(repository), True)
    assert (location.owner, location.name) == ("tka-fixtures", "handbook")
    assert not sentinel.exists()


def test_bare_repository_and_missing_origin(tmp_path: Path) -> None:
    git(tmp_path, "init", "--bare", "--template=")
    with pytest.raises(ValueError, match="origin"):
        source_location(str(tmp_path), True)
    git(tmp_path, "remote", "add", "origin", ORIGIN)
    location = source_location(str(tmp_path), True)
    assert (location.owner, location.name) == ("tka-fixtures", "handbook")


@pytest.mark.parametrize("value", ["", "0", "true", "1"])
def test_local_switch_requires_explicit_one(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", value)
    assert Settings.from_env().allow_local_sources is (value == "1")


def test_git_failure_does_not_expose_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "git", stderr="credential-sentinel")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ValueError, match="Git repository") as error:
        source_location(str(tmp_path), True)
    assert "credential-sentinel" not in str(error.value)
