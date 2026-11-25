"""``scan_code_security`` against the server's local OSV copy: what it says when the copy is missing,
current, stale, or partial -- the part that decides whether "no vulnerabilities" can be believed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.advisories_memory import InMemoryAdvisoryStore
from aura_core.domain.advisories import EcosystemStatus, slim_advisory
from tests.unit.test_open_workspace_tools import make_registry, ok
from tests.unit.test_osv_advisories import osv

NOW = datetime.now(timezone.utc)

REQUIREMENTS_LOCK = "requests==2.19.0\nflask==3.0.0\n"
PACKAGE_LOCK = json.dumps({"lockfileVersion": 3, "packages": {
    "": {"name": "app"}, "node_modules/lodash": {"version": "3.10.1"}, "node_modules/left-pad": {"version": "1.3.0"}}})


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "requirements.lock").write_text(REQUIREMENTS_LOCK)
    (tmp_path / "package-lock.json").write_text(PACKAGE_LOCK)
    (tmp_path / "app.py").write_text("x = 1\n")
    return tmp_path


async def filled(*, synced_days_ago: float | None = 0.1, ecosystems=("pypi", "npm")) -> InMemoryAdvisoryStore:
    store = InMemoryAdvisoryStore()
    await store.upsert(slim_advisory(osv("PYSEC-1", "requests", fixed="2.20.0"), "pypi"))
    await store.upsert(slim_advisory(osv("PYSEC-2", "flask", fixed="2.0.0"), "pypi"))  # 3.0.0 is past it
    await store.upsert(slim_advisory(osv("GHSA-3", "lodash", eco="npm", fixed="4.17.21"), "npm"))
    if synced_days_ago is not None:
        stamp = NOW - timedelta(days=synced_days_ago)
        for eco in ecosystems:
            await store.record_sync(EcosystemStatus(eco, 0, stamp, stamp, None, stamp))
    return store


async def scan(repo, store, **kw) -> dict:
    return (await ok(make_registry(repo, advisory_store=store, **kw), "scan_code_security", paths=["."]))["dependency_vulnerabilities"]


class TestAgainstTheLocalCopy:
    async def test_locked_versions_inside_a_range_are_reported_and_fixed_ones_are_not(self, repo) -> None:
        dep = await scan(repo, await filled())
        assert dep["checked"] is True and dep["advisory_source"] == "the server's local OSV copy"
        assert {(v["package"], v["installed"], v["ecosystem"]) for v in dep["vulnerable"]} == {
            ("requests", "2.19.0", "pypi"), ("lodash", "3.10.1", "npm")}
        assert {v["advisory"] for v in dep["vulnerable"]} == {"PYSEC-1", "GHSA-3"}
        assert "stale" not in dep and "reason" not in dep
        assert {d["ecosystem"] for d in dep["data"]} == {"pypi", "npm"}

    async def test_a_vulnerable_lock_makes_the_whole_scan_not_secure(self, repo) -> None:
        out = await ok(make_registry(repo, advisory_store=await filled()), "scan_code_security", paths=["."])
        assert out["secure"] is False

    async def test_a_copy_that_was_never_downloaded_says_the_packages_were_not_checked(self, repo) -> None:
        dep = await scan(repo, InMemoryAdvisoryStore())
        assert dep["checked"] is False and dep["vulnerable"] == []
        assert "NOT checked" in dep["reason"] and "AURA_OSV_SYNC_ENABLED" in dep["reason"]

    async def test_one_ecosystem_missing_is_named_and_the_other_is_still_checked(self, repo) -> None:
        dep = await scan(repo, await filled(ecosystems=("pypi",)))
        assert dep["checked"] is False and "npm" in dep["reason"] and "pypi" not in dep["reason"]
        assert {v["package"] for v in dep["vulnerable"]} == {"requests"}

    async def test_an_old_copy_is_flagged_stale_with_its_age(self, repo) -> None:
        dep = await scan(repo, await filled(synced_days_ago=9), advisory_max_age_days=3)
        assert "older than 3 days" in dep["stale"] and dep["checked"] is True
        assert {d["age_days"] for d in dep["data"]} == {9.0}

    async def test_a_last_sync_error_is_shown(self, repo) -> None:
        store = await filled()
        stamp = NOW - timedelta(days=1)
        await store.record_sync(EcosystemStatus("pypi", 0, stamp, stamp, "FeedError: 503", NOW))
        data = {d["ecosystem"]: d for d in (await scan(repo, store))["data"]}
        assert data["pypi"]["last_error"] == "FeedError: 503"

    async def test_an_advisory_file_still_wins_when_one_is_given(self, repo) -> None:
        (repo / "adv.json").write_text(json.dumps([osv("X-1", "flask", fixed="9.0")]))
        out = await ok(make_registry(repo, advisory_store=await filled()), "scan_code_security",
                       paths=["."], advisories_path="adv.json")
        dep = out["dependency_vulnerabilities"]
        assert dep["advisory_source"] == "adv.json" and {v["package"] for v in dep["vulnerable"]} == {"flask"}

    async def test_without_a_store_the_old_message_stands(self, repo) -> None:
        dep = (await ok(make_registry(repo), "scan_code_security", paths=["."]))["dependency_vulnerabilities"]
        assert dep["checked"] is False and "advisories_path" in dep["reason"]

    async def test_no_lockfile_is_a_warning_not_a_clean_bill(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        dep = await scan(tmp_path, await filled())
        assert dep["checked"] is False and "no lockfile" in dep["warning"]

    def test_the_tool_description_says_which_mode_it_is_in(self, repo) -> None:
        from aura_core.tools.builtin import default_tools

        def description(**kw) -> str:
            tools = default_tools(workspace=repo, enable_analysis_tools=True, **kw)
            return next(t.spec.description for t in tools if t.spec.name == "scan_code_security")

        assert "local OSV copy" in description(advisory_store=InMemoryAdvisoryStore())
        assert "NOT checked" in description()
        assert len(description()) < 1400, "the tool catalogue is part of every planning prompt"


class TestSettingsAndSchedule:
    def test_the_defaults_download_nothing(self) -> None:
        from aura_core.config.settings import AdvisorySettings

        cfg = AdvisorySettings()
        assert cfg.sync_enabled is False and cfg.include_malware is False and cfg.ecosystems == ("pypi", "npm")

    def test_env_overrides_and_a_bad_ecosystem_is_refused(self, monkeypatch) -> None:
        from aura_core.config.settings import AdvisorySettings
        from aura_core.domain.errors import ConfigurationError

        monkeypatch.setenv("AURA_OSV_SYNC_ENABLED", "true")
        monkeypatch.setenv("AURA_OSV_ECOSYSTEMS", "npm")
        monkeypatch.setenv("AURA_OSV_MAX_AGE_DAYS", "7")
        cfg = AdvisorySettings.from_env()
        assert (cfg.sync_enabled, cfg.ecosystems, cfg.max_age_days) == (True, ("npm",), 7)
        monkeypatch.setenv("AURA_OSV_ECOSYSTEMS", "pypi,cargo")
        with pytest.raises(ConfigurationError):
            AdvisorySettings.from_env()

    def test_the_container_hands_the_store_to_the_scan_tool(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        container = Container(load_settings(refresh=True, validate=False))
        assert isinstance(container.advisory_store, InMemoryAdvisoryStore)

    def test_the_job_is_only_scheduled_when_enabled(self, monkeypatch) -> None:
        import importlib

        import celery_app

        assert "osv-advisory-sync" not in celery_app.celery_app.conf.beat_schedule
        monkeypatch.setenv("AURA_OSV_SYNC_ENABLED", "true")
        try:
            reloaded = importlib.reload(celery_app)
            assert reloaded.celery_app.conf.beat_schedule["osv-advisory-sync"]["task"] == "tasks.osv_sync_task"
        finally:
            monkeypatch.delenv("AURA_OSV_SYNC_ENABLED")
            importlib.reload(celery_app)
