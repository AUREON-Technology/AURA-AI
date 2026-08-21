"""Every backend the configuration can name must resolve to a real adapter.

This exists because of a specific failure: `deploy/docker-compose.prod.yml` set
``AURA_VECTOR_BACKEND=qdrant``, ``AURA_DOCUMENT_BACKEND=postgres``, ``AURA_MEMORY_BACKEND=postgres``
and ``AURA_CACHE_BACKEND=redis``, and three of the four adapter modules had never been written.
Settings validation passed, the image built, and the gateway crash-looped on
``ModuleNotFoundError`` at ``Container.__init__``. Nothing in the suite could catch it, because
every test built a container with in-memory backends -- the one combination that worked.

Two rules:

1. **Each backend name accepted by settings has an importable adapter.** Parameterised over the
   names, so adding a name to ``_KINDS``-style lists without writing the adapter fails here.
2. **The production compose file's backend selection actually builds.** Read from the compose file
   rather than restated, so the two cannot drift -- restating it is what let the gap exist.

No connection is opened. Every adapter builds its client lazily, which is itself part of the
contract: constructing the container happens during settings validation and in every test, and must
not require running infrastructure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "deploy" / "docker-compose.prod.yml"

#: (setting suffix, accepted values) for every backend the container dispatches on.
BACKEND_CHOICES = [
    ("VECTOR_BACKEND", ["memory", "qdrant"]),
    ("DOCUMENT_BACKEND", ["memory", "postgres"]),
    ("MEMORY_BACKEND", ["memory", "postgres"]),
    ("CACHE_BACKEND", ["memory", "redis"]),
]


def _base_env(monkeypatch) -> None:
    """The minimum a container needs, with every backend left at its default.

    Clears inherited AURA_* first so a value set in the developer's shell cannot make a case pass
    or fail for a reason the test does not state.
    """
    for key in [k for k in os.environ if k.startswith("AURA_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AURA_LLM_PROVIDERS", "local")
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_KIND", "openai_compatible")
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("AURA_PROVIDER_LOCAL_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("AURA_JWT_SECRET", "x" * 40)
    # Connection strings for whichever backends a case turns on. Never dialled -- clients are lazy.
    monkeypatch.setenv("AURA_QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("AURA_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("AURA_DATABASE_URL", "postgresql+asyncpg://aura@postgres/aura")


def _build(monkeypatch, **backends):
    from aura_core.config.settings import AuraSettings
    from aura_core.container import Container

    _base_env(monkeypatch)
    for suffix, value in backends.items():
        monkeypatch.setenv(f"AURA_{suffix}", value)
    settings = AuraSettings.from_env()
    settings.validate()
    return Container(settings)


class TestEveryBackendNameResolves:
    @pytest.mark.parametrize(
        "suffix,value",
        [(suffix, value) for suffix, values in BACKEND_CHOICES for value in values],
    )
    def test_the_container_builds_with_this_backend(self, monkeypatch, suffix, value) -> None:
        """A name settings accepts but the container cannot construct is a crash-loop in
        production and nothing at all in CI. That asymmetry is what this closes."""
        container = _build(monkeypatch, **{suffix: value})
        assert container is not None

    def test_an_unknown_backend_name_is_refused_rather_than_ignored(self, monkeypatch) -> None:
        from aura_core.domain.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            _build(monkeypatch, VECTOR_BACKEND="cassandra")


class TestTheProductionComposeSelectionBuilds:
    """Read from the compose file, not restated here.

    Restating it is precisely how the gap survived: the compose file asked for four backends and
    the test suite asked for none of them.
    """

    @staticmethod
    def _compose_backends() -> dict[str, str]:
        if not COMPOSE.exists():  # pragma: no cover - the file is committed
            pytest.skip(f"{COMPOSE} not found")
        text = COMPOSE.read_text(encoding="utf-8")
        found = {}
        for suffix, _ in BACKEND_CHOICES:
            # `AURA_VECTOR_BACKEND: qdrant`, tolerating quotes and extra spacing.
            match = re.search(rf"AURA_{suffix}:\s*[\"']?([a-z_]+)[\"']?", text)
            if match:
                found[suffix] = match.group(1)
        return found

    def test_the_compose_file_names_backends_we_can_build(self, monkeypatch) -> None:
        backends = self._compose_backends()
        assert backends, "expected the production compose file to select backends explicitly"
        container = _build(monkeypatch, **backends)
        assert container is not None

    def test_the_compose_file_does_not_select_in_memory_backends(self) -> None:
        """In-memory backends are refused in production by settings validation, so a compose file
        naming one would fail to boot for a different reason. Asserted here so the two checks
        cannot disagree."""
        for suffix, value in self._compose_backends().items():
            assert value != "memory", (
                f"AURA_{suffix} is 'memory' in the production compose file, which "
                f"AuraSettings.validate() refuses in production"
            )

    def test_every_backend_the_compose_file_names_is_a_known_choice(self) -> None:
        """Catches a typo in the compose file, which would otherwise surface only as a boot
        failure on the VPS."""
        known = {suffix: values for suffix, values in BACKEND_CHOICES}
        for suffix, value in self._compose_backends().items():
            assert value in known[suffix], (
                f"AURA_{suffix}={value!r} in the compose file is not one of {known[suffix]}"
            )


class TestAdaptersConnectLazily:
    """Part of the contract, not an implementation detail: the container is constructed during
    settings validation and in every test that needs one."""

    def test_building_the_production_selection_opens_no_sockets(self, monkeypatch) -> None:
        container = _build(
            monkeypatch,
            VECTOR_BACKEND="qdrant",
            DOCUMENT_BACKEND="postgres",
            MEMORY_BACKEND="postgres",
            CACHE_BACKEND="redis",
        )
        assert container.vectors._client is None
        assert container.cache_backend._client is None
        assert container.documents._pool._pool is None
        assert container.graph._pool._pool is None
        assert container.feedback._pool._pool is None

    def test_both_postgres_stores_share_one_pool(self, monkeypatch) -> None:
        """Separate pools would double the connection count per worker against a Postgres
        configured for a modest max_connections."""
        container = _build(
            monkeypatch, DOCUMENT_BACKEND="postgres", MEMORY_BACKEND="postgres"
        )
        assert container.documents._pool is container.memory_store._pool
        assert container.graph._pool is container.documents._pool
        assert container.feedback._pool is container.documents._pool
