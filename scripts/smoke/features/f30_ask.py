"""Grounded answers over an ingested handbook, including restart persistence."""

from pathlib import Path
from uuid import uuid4

from app.ask import UNKNOWN_ANSWER, KnownAnswer
from app.git import git
from app.sources import Source
from scripts.smoke.core import Context, register_handbook


def inspect_ask(ctx: Context, source: Source) -> dict[str, object]:
    response = ctx.client.post(
        "/api/ask",
        json={"question": "When are deploys frozen?", "source_ids": [str(source.id)]},
    )
    assert response.status_code == 200
    result = KnownAnswer.model_validate(response.json())
    assert result.known and "Fridays after 14:00 UTC" in result.answer
    assert result.citations
    assert "docs/deploy.md" in {c.path for c in result.citations}
    for citation in result.citations:
        assert citation.source_id == source.id
        assert citation.commit == source.indexed_commit
        content = git(Path(source.url), "show", f"{citation.commit}:{citation.path}")
        lines = content.splitlines()
        assert (
            citation.snippet.splitlines()
            == lines[citation.start_line - 1 : citation.end_line]
        )
        assert "Fridays after 14:00 UTC" in citation.snippet
        assert citation.url == (
            f"https://github.com/tka-fixtures/handbook/blob/{citation.commit}/"
            f"{citation.path}#L{citation.start_line}-L{citation.end_line}"
        )
    for question, selection in [
        ("What is the parental leave policy?", [str(source.id)]),
        ("When are deploys frozen?", []),
        ("When are deploys frozen?", [str(uuid4())]),
    ]:
        unknown = ctx.client.post(
            "/api/ask", json={"question": question, "source_ids": selection}
        )
        assert unknown.status_code == 200
        assert unknown.json() == {
            "known": False,
            "answer": UNKNOWN_ANSWER,
            "citations": [],
        }
    return result.model_dump(mode="json")


def before_restart(ctx: Context) -> None:
    source = register_handbook(ctx.client, ctx.state)
    ctx.sources.append(source)
    result = inspect_ask(ctx, source)
    ctx.data["ask_source"] = source
    ctx.data["ask_before"] = result
    ctx.observations["ask_before_restart"] = result
    ctx.observations["ask_unknown"] = {
        "known": False,
        "answer": UNKNOWN_ANSWER,
        "citations": [],
    }


def after_restart(ctx: Context) -> None:
    source = ctx.data["ask_source"]
    assert isinstance(source, Source)
    result = inspect_ask(ctx, source)
    assert result == ctx.data["ask_before"]
    ctx.observations["ask_after_restart"] = result
