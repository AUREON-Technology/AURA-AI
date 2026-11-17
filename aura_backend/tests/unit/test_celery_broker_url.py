"""Which Redis the Celery broker and result backend connect to.

The broker used to be built only from ``REDIS_HOST`` (default ``localhost``) with port 6379
hard-coded. Railway's managed Redis hands the service one ``AURA_REDIS_URL`` (host, port and
password in one string) and no ``REDIS_HOST``, so on that target every ``.delay()`` from the
gateway went to ``localhost:6379`` and failed, and every job status read ``UNKNOWN``. The broker
now resolves the URL the same way ``infrastructure.redis_client`` and the v3 container already
do: the full URL first, the discrete compose-style variables only as a fallback.
"""

from __future__ import annotations

import importlib

import pytest

from celery_app import broker_url


class TestBrokerUrl:
    def test_the_full_url_wins(self) -> None:
        url = "redis://default:s3cret@redis.railway.internal:6380/2"
        env = {"AURA_REDIS_URL": url, "REDIS_HOST": "ignored", "AURA_REDIS_PASSWORD": "ignored"}
        assert broker_url(env) == url

    def test_the_legacy_unprefixed_url_is_accepted(self) -> None:
        assert broker_url({"REDIS_URL": "redis://cache:6379/0"}) == "redis://cache:6379/0"

    def test_the_prefixed_url_beats_the_legacy_one(self) -> None:
        env = {"AURA_REDIS_URL": "redis://a:6379/0", "REDIS_URL": "redis://b:6379/0"}
        assert broker_url(env) == "redis://a:6379/0"

    def test_discrete_compose_variables_still_work_without_a_url(self) -> None:
        env = {"REDIS_HOST": "redis", "AURA_REDIS_PASSWORD": "pw"}
        assert broker_url(env) == "redis://:pw@redis:6379/0"

    def test_no_password_means_no_auth_segment(self) -> None:
        assert broker_url({"REDIS_HOST": "redis"}) == "redis://redis:6379/0"

    def test_nothing_set_keeps_the_local_default(self) -> None:
        assert broker_url({}) == "redis://localhost:6379/0"

    def test_a_password_file_is_read_when_no_url_is_given(self, tmp_path) -> None:
        secret = tmp_path / "redis_password"
        secret.write_text("from-file\n", encoding="utf-8")
        env = {"REDIS_HOST": "redis", "AURA_REDIS_PASSWORD_FILE": str(secret)}
        assert broker_url(env) == "redis://:from-file@redis:6379/0"


@pytest.mark.parametrize("variable", ["AURA_REDIS_URL", "REDIS_URL"])
def test_the_configured_celery_app_uses_the_url(monkeypatch, variable) -> None:
    """The module-level app, not just the helper: this is what workers, beat and the gateway's
    ``.delay()`` calls actually read."""
    url = "redis://default:pw@redis.railway.internal:6379/0"
    for name in ("AURA_REDIS_URL", "REDIS_URL", "REDIS_HOST", "AURA_REDIS_PASSWORD",
                 "AURA_REDIS_PASSWORD_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, url)

    import celery_app as module

    try:
        reloaded = importlib.reload(module)
        assert reloaded.celery_app.conf.broker_url == url
        assert reloaded.celery_app.conf.result_backend == url
    finally:
        monkeypatch.undo()
        importlib.reload(module)
