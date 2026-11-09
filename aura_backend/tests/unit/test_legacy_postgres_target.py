"""`legacy_postgres.py` gives the legacy integration suites a Postgres without Docker (audit G-05).

The seven `aura_backend/test_*_integration.py` suites skipped everywhere without a Docker daemon,
including CI's `postgres` job, which has a real server. With `AURA_TEST_DATABASE_URL` set they get
a fresh database on that server instead. The database round-trip itself runs in that job; here the
URL handling and the choice between the two paths are pinned without a server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import legacy_postgres  # noqa: E402

SERVER = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"


class TestUrls:
    def test_a_fresh_database_keeps_server_and_credentials(self) -> None:
        assert legacy_postgres.with_database(SERVER, "aura_legacy_abc") == (
            "postgresql://postgres:postgres@127.0.0.1:5432/aura_legacy_abc"
        )

    def test_the_url_is_reported_in_the_scheme_the_suites_rewrite(self) -> None:
        """Every suite does `.replace("postgresql+psycopg2", ...)` on what testcontainers returns."""
        assert legacy_postgres.as_psycopg2_url(SERVER).startswith("postgresql+psycopg2://")
        assert legacy_postgres.as_psycopg2_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"

    def test_each_server_database_gets_its_own_name(self) -> None:
        a = legacy_postgres.ServerDatabase(SERVER)
        b = legacy_postgres.ServerDatabase(SERVER)
        assert a.get_connection_url() != b.get_connection_url()
        assert a.get_connection_url().startswith("postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/aura_legacy_")


class TestWhichPath:
    def test_a_server_url_makes_the_suites_available_without_docker(self, monkeypatch) -> None:
        monkeypatch.setenv(legacy_postgres.ENV_VAR, SERVER)
        monkeypatch.setattr(legacy_postgres, "_docker_available", lambda: False)
        assert legacy_postgres.available()
        assert isinstance(legacy_postgres.container(), legacy_postgres.ServerDatabase)

    def test_without_either_the_suites_skip(self, monkeypatch) -> None:
        monkeypatch.delenv(legacy_postgres.ENV_VAR, raising=False)
        monkeypatch.setattr(legacy_postgres, "_docker_available", lambda: False)
        assert not legacy_postgres.available()

    def test_docker_alone_still_counts(self, monkeypatch) -> None:
        monkeypatch.delenv(legacy_postgres.ENV_VAR, raising=False)
        monkeypatch.setattr(legacy_postgres, "_docker_available", lambda: True)
        assert legacy_postgres.available()


class TestTheSuitesUseIt:
    @pytest.mark.parametrize("suite", sorted(p.name for p in BACKEND.glob("test_*_integration.py")))
    def test_no_integration_suite_starts_its_own_container_or_checks_docker_itself(self, suite: str) -> None:
        text = (BACKEND / suite).read_text(encoding="utf-8")
        assert "PostgresContainer(" not in text, f"{suite} still constructs its own container"
        assert "def _docker_available" not in text, f"{suite} still has its own Docker probe"
        assert "legacy_postgres" in text
