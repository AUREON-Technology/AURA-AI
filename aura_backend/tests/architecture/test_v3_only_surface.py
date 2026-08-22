"""Fitness: the production gateway serves /api/v3 only."""

from __future__ import annotations

from fastapi import routing

from aura_service import app

_UNVERSIONED = (
    "/api/chat",
    "/api/rag",
    "/api/memory",
    "/api/automation",
    "/api/learning",
    "/api/telemetry",
)


def _api_paths() -> list[str]:
    # FastAPI >=0.116 represents an included sub-router as a lazy
    # _IncludedRouter wrapper on app.routes -- it has no .path attribute, so a
    # naive `getattr(route, "path", "")` walk silently sees zero routes.
    # iter_route_contexts is the same resolver FastAPI's own app.openapi()
    # uses internally; it recurses through nested routers and yields the
    # fully-prefixed path.
    return sorted(
        {
            ctx.path
            for ctx in routing.iter_route_contexts(app.routes)
            if ctx.path and ctx.path.startswith("/api/")
        }
    )


_REQUIRED_PREFIXES = (
    "/api/v3/auth",
    "/api/v3/sync",
    "/api/v3/insights",
    "/api/v3/automation",
    "/api/v3/learning",
    "/api/v3/chat",
    "/api/v3/memory",
)


def test_every_api_route_is_under_v3() -> None:
    paths = _api_paths()
    assert paths, "expected /api/v3 routes to be registered"
    for path in paths:
        assert path == "/api/v3" or path.startswith("/api/v3/"), path


def test_platform_and_kernel_prefixes_are_mounted() -> None:
    paths = _api_paths()
    for prefix in _REQUIRED_PREFIXES:
        assert any(path == prefix or path.startswith(prefix + "/") for path in paths), prefix


def test_legacy_api_prefixes_are_not_registered() -> None:
    paths = _api_paths()
    leftover = [path for path in paths if path.startswith("/api/v2/")]
    assert leftover == []

    for prefix in _UNVERSIONED:
        leftover = [
            path
            for path in paths
            if path == prefix or path.startswith(prefix + "/")
        ]
        assert leftover == [], leftover
