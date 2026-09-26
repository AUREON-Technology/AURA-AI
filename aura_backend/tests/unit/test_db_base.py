"""``with_asyncpg_driver`` must rewrite the bare Postgres DSN a managed provider (Railway,
Heroku, RDS) hands out into the ``+asyncpg`` form ``create_async_engine`` needs -- without it,
SQLAlchemy's default dialect resolution reaches for the sync ``psycopg2`` driver, which isn't
installed in this async-only image, and every request touching the database 500s with
``ModuleNotFoundError: No module named 'psycopg2'`` (confirmed live against production).
"""

from __future__ import annotations

from infrastructure.db.base import with_asyncpg_driver


def test_it_rewrites_a_bare_postgresql_scheme() -> None:
    assert (
        with_asyncpg_driver("postgresql://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_it_rewrites_the_postgres_scheme_alias() -> None:
    assert (
        with_asyncpg_driver("postgres://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_it_leaves_an_already_asyncpg_dsn_unchanged() -> None:
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    assert with_asyncpg_driver(url) == url


def test_it_leaves_an_unrelated_scheme_unchanged() -> None:
    url = "sqlite+aiosqlite:///:memory:"
    assert with_asyncpg_driver(url) == url
