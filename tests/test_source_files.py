from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, Mock
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import db, main, source_files
from app.chunking import chunk_text
from app.config import Settings
from app.ingest import CloneSelectError, SelectedFile, SourceSpec, clone_and_select
from app.ingest.contracts import ErrorCode
from app.source_files import SourceFileError, fetch_source_file
from tests.handbook import create_handbook_repo
from tests.test_clone_select import LIMITS, commit
from tests.test_http import ClientFactory, wait_for_health


@dataclass
class FileClient:
    client: TestClient
    source_id: UUID
    row: dict[str, object]
    lookup: Mock
    reader: Mock
    settings: Settings

    @property
    def url(self) -> str:
        return f"/api/sources/{self.source_id}/file"


@pytest.fixture
def file_client(
    monkeypatch: pytest.MonkeyPatch, client_factory: ClientFactory
) -> Iterator[FileClient]:
    source_id = uuid4()
    row: dict[str, object] = {
        "id": source_id,
        "url": "https://github.com/acme/handbook",
        "owner": "acme",
        "name": "handbook",
        "indexed_commit": "a" * 40,
    }
    connection = MagicMock()
    cursor = (
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    )
    cursor.fetchone.return_value = row
    monkeypatch.setattr(db, "connect", Mock(return_value=connection))
    monkeypatch.setattr(db, "run_migrations", Mock())
    monkeypatch.setattr(db, "probe", Mock())
    reader = Mock(
        return_value=SelectedFile("file.md", "b" * 40, "markdown", 6, "a\nb\nc\n")
    )
    monkeypatch.setattr(source_files, "read_cached_file", reader)
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        yield FileClient(client, source_id, row, cursor, reader, Settings.from_env())


@pytest.mark.parametrize(
    ("query", "text", "bounds"),
    [
        ({}, "a\nb\nc\n", (1, 3)),
        ({"start": "2"}, "b\nc\n", (2, 3)),
        ({"end": "2"}, "a\nb\n", (1, 2)),
        ({"start": "2", "end": "2"}, "b\n", (2, 2)),
        ({"end": "99"}, "a\nb\nc\n", (1, 3)),
        ({"start": "4"}, "", (None, None)),
    ],
)
def test_ranges(
    file_client: FileClient,
    query: dict[str, str],
    text: str,
    bounds: tuple[int | None, int | None],
) -> None:
    f = file_client
    response = f.client.get(f.url, params={"path": "file.md", **query})
    assert response.status_code == 200
    assert response.json() == {
        "source_id": str(f.source_id),
        "path": "file.md",
        "commit": "a" * 40,
        "start_line": bounds[0],
        "end_line": bounds[1],
        "total_lines": 3,
        "content": text,
    }
    f.lookup.execute.assert_called_once_with(
        "SELECT id, url, owner, name, indexed_commit FROM sources WHERE id = %s",
        (f.source_id,),
    )


@pytest.mark.parametrize(
    "text",
    ["", "\n", "tail", "a\n", "\ufeffa\r\nb\nc\r\nd", "a\rb\fc\u2028d\u2029e\nlast"],
)
def test_lines_match_chunks(file_client: FileClient, text: str) -> None:
    f = file_client
    f.reader.return_value = SelectedFile(
        "file.md", "b" * 40, "markdown", len(text.encode()), text
    )
    full = f.client.get(f.url, params={"path": "file.md"}).json()
    assert full["content"] == text
    assert full["total_lines"] == text.count("\n") + int(
        bool(text) and not text.endswith("\n")
    )
    if not text:
        assert full["start_line"] is full["end_line"] is None
    for chunk in chunk_text(
        "file.md", text, kind="markdown", window_lines=2, overlap_lines=1
    ):
        result = fetch_source_file(
            f.source_id,
            path=chunk.path,
            start=chunk.start_line,
            end=chunk.end_line,
            settings=f.settings,
        )
        assert result.content == chunk.content
        assert (result.start_line, result.end_line) == (
            chunk.start_line,
            chunk.end_line,
        )
        assert "content=" not in repr(result)


@pytest.mark.parametrize(
    "path",
    [
        "../secret.md",
        "/secret.md",
        "C:/secret.md",
        "C:secret.md",
        "\\\\host\\secret.md",
        "a/../secret.md",
        "a/./b.md",
        "a//b.md",
        "a\\..\\secret.md",
        "a\0b.md",
        "a\x1fb.md",
        "a\x7fb.md",
    ],
)
def test_invalid_paths_fail_before_storage(file_client: FileClient, path: str) -> None:
    f = file_client
    response = f.client.get(f.url, params={"path": path})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid source file path"}
    f.reader.assert_not_called()
    f.lookup.execute.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        {"start": "0"},
        {"end": "-1"},
        {"start": "3", "end": "2"},
        {"start": "1.5"},
        {"start": "true"},
        {"end": "secret"},
    ],
)
def test_invalid_http_ranges(file_client: FileClient, query: dict[str, str]) -> None:
    f = file_client
    response = f.client.get(f.url, params={"path": "file.md", **query})
    assert response.status_code == 422
    assert "secret" not in response.text
    f.reader.assert_not_called()
    f.lookup.execute.assert_not_called()


def test_missing_inputs_and_direct_validation(file_client: FileClient) -> None:
    f = file_client
    for params in ({}, {"path": ""}):
        assert f.client.get(f.url, params=params).status_code == 404
    for bound in (True, False, 0, -1):
        with pytest.raises(SourceFileError, match="Invalid source line range"):
            fetch_source_file(
                f.source_id, path="file.md", start=bound, settings=f.settings
            )
    with pytest.raises(SourceFileError, match="Invalid source file path"):
        fetch_source_file(f.source_id, path="bad\ud800.md", settings=f.settings)
    assert (
        f.client.get(
            "/api/sources/invalid/file", params={"path": "file.md"}
        ).status_code
        == 422
    )
    f.lookup.fetchone.return_value = None
    assert f.client.get(f.url, params={"path": "file.md"}).status_code == 404
    f.row["indexed_commit"] = None
    f.lookup.fetchone.return_value = f.row
    assert f.client.get(f.url, params={"path": "file.md"}).status_code == 404
    f.reader.assert_not_called()


def test_error_mapping(
    file_client: FileClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = file_client
    codes: tuple[ErrorCode, ...] = (
        "not_found",
        "busy",
        "invalid_storage",
        "source_mismatch",
        "git_unavailable",
        "timeout",
        "git_failed",
    )
    for code in codes:
        f.reader.side_effect = CloneSelectError(code)
        response = f.client.get(f.url, params={"path": "file.md"})
        assert response.status_code == (404 if code == "not_found" else 503)
        assert response.headers.get("Retry-After") == ("1" if code == "busy" else None)
    monkeypatch.setattr(
        db,
        "connect",
        Mock(side_effect=psycopg.OperationalError("secret database credentials")),
    )
    response = f.client.get(f.url, params={"path": "file.md"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Source storage is unavailable"}


def test_commit_is_captured_once(file_client: FileClient) -> None:
    f = file_client

    def advance(
        source: SourceSpec, *, repo_url: str, commit: str, path: str, settings: Settings
    ) -> SelectedFile:
        f.row["indexed_commit"] = "c" * 40
        return SelectedFile(path, "b" * 40, "markdown", 1, commit[0])

    f.reader.side_effect = advance
    response = f.client.get(f.url, params={"path": "file.md"}).json()
    assert response["commit"] == "a" * 40 and response["content"] == "a"
    response = f.client.get(f.url, params={"path": "file.md"}).json()
    assert response["commit"] == "c" * 40 and response["content"] == "c"


@pytest.mark.database
def test_registry_cache_http_lifecycle(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client_factory: ClientFactory,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TKA_ALLOW_LOCAL_SOURCES", "1")
    repository = create_handbook_repo(tmp_path)
    settings = Settings.from_env()
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        response = client.post("/api/sources", json={"url": str(repository)})
        assert response.status_code == 202
        source_id = UUID(response.json()["id"])
        url = f"/api/sources/{source_id}/file"
        assert client.get(url, params={"path": "docs/deploy.md"}).status_code == 404
        source = SourceSpec(source_id, response.json()["url"])
        first = clone_and_select(source, settings=settings, limits=LIMITS)
        with db.connect(database_url) as connection:
            connection.execute(
                "UPDATE sources SET indexed_commit = %s, status = 'failed' "
                "WHERE id = %s",
                (first.commit_sha, source_id),
            )
        expected = next(f.content for f in first.files if f.path == "docs/deploy.md")
        (repository / "docs/deploy.md").write_bytes(b"new deployment\n")
        (repository / "docs/legacy-deploy.md").unlink()
        (repository / "docs/onboarding.md").rename(repository / "docs/start.md")
        commit(repository)
        second = clone_and_select(source, settings=settings, limits=LIMITS)
        (repository / "docs/deploy.md").write_bytes(b"working tree")
        full = client.get(url, params={"path": "docs/deploy.md"}).json()
        assert full["content"] == expected and full["commit"] == first.commit_sha
        window = client.get(
            url, params={"path": "docs/deploy.md", "start": 14, "end": 18}
        ).json()
        assert window["content"] == "".join(expected.splitlines(keepends=True)[13:18])
        assert (window["start_line"], window["end_line"]) == (14, 18)
        with db.connect(database_url) as connection:
            connection.execute(
                "UPDATE sources SET indexed_commit = %s WHERE id = %s",
                (second.commit_sha, source_id),
            )
            assert connection.execute("SELECT count(*) FROM files").fetchone() == (0,)
        for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
            assert client.get(url, params={"path": path}).status_code == 404
        assert (
            client.get(url, params={"path": "docs/start.md"}).json()["commit"]
            == second.commit_sha
        )
    with client_factory(main.create_app()) as client:
        wait_for_health(client, 200)
        result = client.get(url, params={"path": "docs/deploy.md"}).json()
        assert (
            result["content"] == "new deployment\n"
            and result["commit"] == second.commit_sha
        )
