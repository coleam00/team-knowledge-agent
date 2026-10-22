"""Capabilities the build order has not reached yet answer 404 (MISSION Gate 3, staged
delivery). An increment that builds one of them removes its entry here and adds its
own feature module."""

import httpx

from scripts.smoke.core import Context


def absent_features(client: httpx.Client) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for method, path in [
        ("POST", "/api/sources/1/reindex"),
        ("POST", "/mcp"),
    ]:
        response = client.request(method, path)
        assert response.status_code == 404
        observations.append(
            {"method": method, "path": path, "status": 404, "stage": "not yet built"}
        )
    return observations


def before_restart(ctx: Context) -> None:
    ctx.observations["unbuilt_features"] = absent_features(ctx.client)
