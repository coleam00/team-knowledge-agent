"""Indexed file reads: lines are served at the indexed commit, ranges clip at EOF, and
deleted or renamed paths follow the newly indexed commit. Its upstream is mutated, so
it registers its own handbook rather than sharing the search fixtures."""

from pathlib import Path

import httpx

from app.config import Settings
from app.db import connect
from app.git import git
from app.ingest import CloneLimits, SourceSpec, clone_and_select
from app.sources import Source
from app.text_lines import physical_lines
from scripts.smoke.core import Context, register_handbook


def verify_indexed_file_reads(
    client: httpx.Client,
    source: Source,
    settings: Settings,
    observations: dict[str, object],
) -> Source:
    spec = SourceSpec(source.id, source.url)
    limits = CloneLimits(100, 10_000_000, 30)
    repository = Path(source.url)
    deploy = repository / "docs/deploy.md"
    deploy.write_bytes(deploy.read_bytes() + b"\nRange line 17\nRange line 18\nTail\n")
    git(repository, "add", "--force", ".")
    git(repository, "commit", "-m", "Prepare five-line fetch range")
    first = clone_and_select(spec, settings=settings, limits=limits)
    with connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE sources SET indexed_commit = %s WHERE id = %s",
            (first.commit_sha, source.id),
        )
    url = f"/api/sources/{source.id}/file"
    expected = next(
        file.content for file in first.files if file.path == "docs/deploy.md"
    )
    renamed_content = next(
        file.content for file in first.files if file.path == "docs/onboarding.md"
    )
    full = client.get(url, params={"path": "docs/deploy.md"})
    assert full.status_code == 200
    assert full.json()["content"] == expected
    assert full.json()["commit"] == first.commit_sha
    (repository / "docs/deploy.md").write_bytes(b"Updated deployment at C2\n")
    (repository / "docs/legacy-deploy.md").unlink()
    (repository / "docs/onboarding.md").rename(repository / "docs/start.md")
    git(repository, "add", "--force", ".")
    git(repository, "commit", "-m", "Update smoke handbook")
    second = clone_and_select(spec, settings=settings, limits=limits)
    (repository / "docs/deploy.md").write_bytes(b"Uncommitted working content")
    assert client.get(url, params={"path": "docs/deploy.md"}).json() == full.json()
    window = client.get(url, params={"path": "docs/deploy.md", "start": 14, "end": 18})
    assert window.status_code == 200
    assert window.json()["content"] == "".join(physical_lines(expected)[13:18])
    assert len(physical_lines(window.json()["content"])) == 5
    assert window.json()["commit"] == first.commit_sha
    assert (window.json()["start_line"], window.json()["end_line"]) == (14, 18)
    clipped = client.get(url, params={"path": "docs/deploy.md", "end": 999})
    assert clipped.status_code == 200 and clipped.json() == full.json()
    for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
        old = client.get(url, params={"path": path})
        assert old.status_code == 200 and old.json()["commit"] == first.commit_sha
    with connect(settings.database_url) as connection:
        connection.execute(
            "UPDATE sources SET indexed_commit = %s WHERE id = %s",
            (second.commit_sha, source.id),
        )
    for path in ("docs/legacy-deploy.md", "docs/onboarding.md"):
        assert client.get(url, params={"path": path}).status_code == 404
    renamed = client.get(url, params={"path": "docs/start.md"})
    assert renamed.status_code == 200 and renamed.json()["commit"] == second.commit_sha
    assert renamed.json()["content"] == renamed_content
    edited = client.get(url, params={"path": "docs/deploy.md"})
    assert edited.status_code == 200
    assert edited.json()["content"] == "Updated deployment at C2\n"
    assert edited.json()["commit"] == second.commit_sha
    observations["indexed_file_reads"] = {
        "setup": "acquisition library and indexed_commit record updates; no worker",
        "source_id": str(source.id),
        "commit_c": first.commit_sha,
        "commit_c2": second.commit_sha,
        "full_file_at_c": True,
        "range_at_c": [14, 18],
        "range_response": window.json(),
        "eof_clipped": True,
        "pinned_after_acquiring_c2": True,
        "deleted_and_renamed_old_paths": 404,
        "new_path_status": 200,
        "renamed_response": renamed.json(),
        "updated_content_at_c2": True,
    }
    return Source.model_validate(client.get(f"/api/sources/{source.id}").json())


def before_restart(ctx: Context) -> None:
    file_source = verify_indexed_file_reads(
        ctx.client,
        register_handbook(ctx.client, ctx.state),
        ctx.settings,
        ctx.observations,
    )
    ctx.sources.append(file_source)
    ctx.data["file_source"] = file_source


def after_restart(ctx: Context) -> None:
    file_source = ctx.data["file_source"]
    assert isinstance(file_source, Source)
    file = ctx.client.get(
        f"/api/sources/{file_source.id}/file", params={"path": "docs/deploy.md"}
    )
    assert file.status_code == 200
    assert file.json()["commit"] == file_source.indexed_commit
    assert file.json()["content"] == "Updated deployment at C2\n"
    ctx.observations["indexed_file_after_restart"] = {
        "status": 200,
        "commit": file.json()["commit"],
        "content_matches": True,
    }
