"""The local OSV advisory copy: domain helpers, the store contract (memory and, opt-in, Postgres), the
feed over real HTTP against a local server that serves the bucket's layout, and the sync job.

REAL: the feed adapter's HTTP code, zip handling, the store, the sync orchestration. SIMULATED: only the
bucket itself -- a stdlib HTTP server serving ``modified_id.csv``, ``<ID>.json`` and ``all.zip`` in the
shapes ``storage.googleapis.com/osv-vulnerabilities`` serves (checked against the live bucket).

    AURA_TEST_POSTGRES_URL=postgresql://postgres@localhost:55437/aura_test pytest tests/unit/test_osv_advisories.py
"""

from __future__ import annotations

import io
import json
import os
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aura_core.adapters.advisories_memory import InMemoryAdvisoryStore
from aura_core.adapters.osv_feed import FeedError, HttpOsvFeed
from aura_core.domain import advisories as adv
from aura_core.domain.advisories import EcosystemStatus
from aura_core.learning.osv_sync import run_osv_sync

PG_URL = os.environ.get("AURA_TEST_POSTGRES_URL", "")
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def osv(adv_id: str, package: str, *, introduced="0", fixed="2.0.0", modified="2026-11-20T10:00:00.123456789Z",
        eco="PyPI", withdrawn=None, extra_packages=()) -> dict:
    doc = {
        "schema_version": "1.9.2", "id": adv_id, "modified": modified, "aliases": ["CVE-2026-1"],
        "summary": f"{package} problem", "details": "long text " * 50,
        "affected": [{"package": {"name": name, "ecosystem": eco},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": introduced}, {"fixed": fixed}]}]}
                     for name in (package, *extra_packages)],
        "references": [{"type": "WEB", "url": "https://example.com/" + "x" * 200}],
        "database_specific": {"severity": "HIGH"},
    }
    if withdrawn:
        doc["withdrawn"] = withdrawn
    return doc


# -- domain -------------------------------------------------------------------------------------


class TestDomain:
    def test_osv_timestamps_with_nanoseconds_parse(self) -> None:
        assert adv.parse_osv_time("2026-11-25T10:00:02.445764108Z") == datetime(
            2026, 11, 25, 10, 0, 2, 445764, tzinfo=timezone.utc)
        assert adv.parse_osv_time("2026-11-25T10:00:02Z").tzinfo is not None
        assert adv.parse_osv_time("garbage") is None and adv.parse_osv_time(None) is None

    def test_package_names_are_normalised_like_the_lock_parser(self) -> None:
        assert adv.normalize_package("Django_Rest.Framework") == "django-rest-framework"

    def test_an_advisory_is_cut_down_per_affected_package_and_free_text_dropped(self) -> None:
        rows = adv.slim_advisory(osv("PYSEC-1", "Foo_Bar", extra_packages=("baz",)), "pypi")
        assert {r.package for r in rows} == {"foo-bar", "baz"}
        payload = rows[0].payload
        assert set(payload) >= {"id", "aliases", "summary", "affected", "database_specific"}
        assert "details" not in payload and "references" not in payload
        assert len(json.dumps(payload)) < 800

    def test_other_ecosystems_and_malformed_documents_are_ignored(self) -> None:
        assert adv.slim_advisory(osv("X", "left-pad", eco="npm"), "pypi") == []
        assert adv.slim_advisory({"id": "X"}, "pypi") == []
        assert adv.slim_advisory({"modified": "2026-01-01T00:00:00Z", "affected": []}, "pypi") == []

    def test_a_huge_version_list_is_dropped_but_the_ranges_stay(self) -> None:
        doc = osv("PYSEC-2", "big")
        doc["affected"][0]["versions"] = [f"1.{i}" for i in range(5000)]
        payload = adv.slim_advisory(doc, "pypi")[0].payload
        assert "versions" not in payload["affected"][0] and payload["affected"][0]["ranges"]


# -- the store contract ---------------------------------------------------------------------------


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemoryAdvisoryStore()
        return
    if not PG_URL:
        pytest.skip("AURA_TEST_POSTGRES_URL not set (opt-in: needs a throwaway Postgres)")
    from aura_core.adapters.postgres_advisories import PostgresAdvisoryStore
    from aura_core.adapters.postgres_store import PostgresPool
    from tests.postgres_migrations import migrate

    pool = PostgresPool(PG_URL)
    migrate(PG_URL)
    raw = await pool._get_pool()
    async with raw.acquire() as conn:
        await conn.execute("TRUNCATE aura_osv_advisories, aura_osv_sync")
    yield PostgresAdvisoryStore(PG_URL, pool=pool)
    await pool.close() if hasattr(pool, "close") else None


class TestStoreContract:
    async def test_lookup_returns_one_merged_advisory_per_id(self, store) -> None:
        await store.upsert(adv.slim_advisory(osv("A-1", "foo", extra_packages=("bar",)), "pypi")
                           + adv.slim_advisory(osv("A-2", "foo", fixed="3.0"), "pypi"))
        found = {a["id"]: a for a in await store.lookup("pypi", ["foo", "bar", "other"])}
        assert set(found) == {"A-1", "A-2"}
        assert {e["package"]["name"] for e in found["A-1"]["affected"]} == {"foo", "bar"}
        assert len(found["A-2"]["affected"]) == 1
        assert await store.lookup("pypi", ["nothing"]) == [] and await store.lookup("npm", ["foo"]) == []
        assert await store.lookup("pypi", []) == []

    async def test_upsert_replaces_and_remove_forgets(self, store) -> None:
        await store.upsert(adv.slim_advisory(osv("A-1", "foo", fixed="2.0"), "pypi"))
        await store.upsert(adv.slim_advisory(osv("A-1", "foo", fixed="9.9"), "pypi"))
        (only,) = await store.lookup("pypi", ["foo"])
        assert only["affected"][0]["ranges"][0]["events"][1] == {"fixed": "9.9"}
        assert await store.remove("pypi", ["A-1", "never-stored"]) >= 1
        assert await store.lookup("pypi", ["foo"]) == [] and await store.remove("pypi", []) == 0

    async def test_status_counts_advisories_once_however_many_packages_they_touch(self, store) -> None:
        await store.upsert(adv.slim_advisory(osv("A-1", "foo", extra_packages=("bar", "baz")), "pypi"))
        await store.upsert(adv.slim_advisory(osv("N-1", "left-pad", eco="npm"), "npm"))
        stamp = datetime(2026, 11, 20, tzinfo=timezone.utc)
        await store.record_sync(EcosystemStatus("pypi", 0, stamp, stamp, None, stamp))
        by = {s.ecosystem: s for s in await store.status()}
        assert by["pypi"].advisories == 1 and by["pypi"].newest_modified == stamp and by["pypi"].synced_at == stamp
        assert by["npm"].advisories == 1 and by["npm"].synced_at is None

    async def test_a_failed_attempt_is_recorded_without_losing_the_last_good_sync(self, store) -> None:
        good = datetime(2026, 11, 18, tzinfo=timezone.utc)
        await store.record_sync(EcosystemStatus("pypi", 0, good, good, None, good))
        later = good + timedelta(days=1)
        await store.record_sync(EcosystemStatus("pypi", 0, good, good, "FeedError: boom", later))
        (row,) = [s for s in await store.status() if s.ecosystem == "pypi"]
        assert row.synced_at == good and row.last_error == "FeedError: boom" and row.last_attempt_at == later


# -- the feed over HTTP -------------------------------------------------------------------------------


class Bucket:
    """A stand-in for the OSV bucket: ``<Eco>/modified_id.csv``, ``<Eco>/<ID>.json``, ``<Eco>/all.zip``."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict]] = {"PyPI": {}, "npm": {}}
        self.hits: list[str] = []
        self.fail: set[str] = set()
        self.hidden: set[str] = set()  # listed in the index but its file 404s
        self.zip_override: bytes | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def do_GET(self):
                outer.hits.append(self.path)
                parts = self.path.strip("/").split("/")
                if len(parts) != 2 or parts[0] not in outer.docs or parts[0] in outer.fail:
                    return self._send(503 if parts[0] in outer.fail else 404, b"")
                eco, name = parts
                docs = outer.docs[eco]
                if name == "modified_id.csv":
                    rows = sorted(docs.values(), key=lambda d: d["modified"], reverse=True)
                    return self._send(200, "".join(f"{d['modified']},{d['id']}\n" for d in rows).encode())
                if name == "all.zip":
                    if outer.zip_override is not None:
                        return self._send(200, outer.zip_override)
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w") as z:
                        for d in docs.values():
                            z.writestr(f"{d['id']}.json", json.dumps(d))
                    return self._send(200, buf.getvalue())
                key = name.removesuffix(".json")
                doc = None if key in outer.hidden else docs.get(key)
                return self._send(200, json.dumps(doc).encode()) if doc else self._send(404, b"")

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def put(self, doc: dict, eco: str = "PyPI") -> None:
        self.docs[eco][doc["id"]] = doc

    def feed(self, **kw) -> HttpOsvFeed:
        return HttpOsvFeed(base_url=self.url, allow_other_host=True, **kw)

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture()
def bucket():
    b = Bucket()
    yield b
    b.close()


class TestFeed:
    def test_in_production_it_may_only_talk_to_the_osv_bucket_over_https(self) -> None:
        assert HttpOsvFeed()  # the default is the real bucket
        for bad in ("http://storage.googleapis.com/osv-vulnerabilities", "https://evil.example/osv",
                    "https://storage.googleapis.com.evil.example/x", "ftp://storage.googleapis.com/x"):
            with pytest.raises(ValueError):
                HttpOsvFeed(base_url=bad)

    async def test_the_index_is_parsed_newest_first_and_junk_lines_are_ignored(self, bucket) -> None:
        bucket.put(osv("A-1", "foo", modified="2026-11-20T10:00:00Z"))
        bucket.put(osv("A-2", "foo", modified="2026-11-21T10:00:00Z"))
        got = await bucket.feed().modified_since("pypi")
        assert [i for _, i in got] == ["A-2", "A-1"] and got[0][0] > got[1][0]

    async def test_fetch_returns_the_document_or_none_and_refuses_a_hostile_id(self, bucket) -> None:
        bucket.put(osv("A-1", "foo"))
        feed = bucket.feed()
        assert (await feed.fetch("pypi", "A-1"))["id"] == "A-1"
        assert await feed.fetch("pypi", "A-404") is None
        for bad in ("../../etc/passwd", "a/b", "a b", "", "x" * 500, "a?b=1"):
            with pytest.raises(FeedError):
                await feed.fetch("pypi", bad)
        assert not any("passwd" in h for h in bucket.hits)

    async def test_the_archive_streams_in_batches(self, bucket) -> None:
        for i in range(7):
            bucket.put(osv(f"A-{i}", "foo"))
        batches = [b async for b in bucket.feed().archive("pypi", batch=3)]
        assert [len(b) for b in batches] == [3, 3, 1]

    async def test_an_archive_over_the_size_cap_is_refused(self, bucket) -> None:
        for i in range(50):
            bucket.put(osv(f"A-{i}", "foo"))
        with pytest.raises(FeedError, match="cap"):
            _ = [b async for b in bucket.feed(archive_max_bytes=500).archive("pypi")]

    async def test_a_broken_archive_and_a_server_error_are_feed_errors(self, bucket) -> None:
        bucket.zip_override = b"this is not a zip"
        with pytest.raises(FeedError, match="zip"):
            _ = [b async for b in bucket.feed().archive("pypi")]
        bucket.fail.add("npm")
        with pytest.raises(FeedError):
            await bucket.feed().modified_since("npm")

    async def test_an_unsupported_ecosystem_is_refused(self, bucket) -> None:
        with pytest.raises(FeedError):
            await bucket.feed().modified_since("rubygems")


# -- the sync job -----------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class TestSync:
    async def test_first_run_downloads_the_archive_then_later_runs_fetch_only_what_changed(self, bucket) -> None:
        for i in range(3):
            bucket.put(osv(f"A-{i}", f"pkg{i}", modified=f"2026-11-1{i}T10:00:00Z"))
        store, clock = InMemoryAdvisoryStore(), Clock()
        first = await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], clock=clock)
        assert first["status"] == "success" and first["ecosystems"]["pypi"]["mode"] == "bootstrap"
        assert first["ecosystems"]["pypi"]["stored"] == 3
        assert any(h.endswith("all.zip") for h in bucket.hits)
        (status,) = await store.status()
        assert status.advisories == 3 and status.synced_at == T0
        assert status.newest_modified == datetime(2026, 11, 12, 10, tzinfo=timezone.utc)

        bucket.hits.clear()
        clock.now = T0 + timedelta(days=1)
        assert (await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], clock=clock)
                )["ecosystems"]["pypi"]["mode"] == "unchanged"
        assert not any(h.endswith("all.zip") or h.endswith(".json") for h in bucket.hits)
        assert (await store.status())[0].synced_at == clock.now, "an unchanged feed is still a fresh copy"

        bucket.put(osv("A-9", "pkg9", modified="2026-11-22T10:00:00Z"))
        bucket.put(osv("A-0", "pkg0", fixed="9.9", modified="2026-11-23T10:00:00Z"))
        bucket.hits.clear()
        later = await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], clock=clock)
        assert later["ecosystems"]["pypi"]["mode"] == "incremental" and later["ecosystems"]["pypi"]["fetched"] == 2
        assert sorted(h for h in bucket.hits if h.endswith(".json")) == ["/PyPI/A-0.json", "/PyPI/A-9.json"]
        (changed,) = await store.lookup("pypi", ["pkg0"])
        assert changed["affected"][0]["ranges"][0]["events"][1] == {"fixed": "9.9"}

    async def test_a_long_gap_falls_back_to_the_archive(self, bucket) -> None:
        for i in range(6):
            bucket.put(osv(f"A-{i}", f"pkg{i}", modified=f"2026-11-0{i + 1}T10:00:00Z"))
        store = InMemoryAdvisoryStore()
        await store.record_sync(EcosystemStatus("pypi", 0, datetime(2026, 10, 1, tzinfo=timezone.utc), T0, None, T0))
        out = await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], max_incremental=3)
        assert out["ecosystems"]["pypi"]["mode"] == "bootstrap"

    async def test_withdrawn_and_deleted_advisories_stop_matching(self, bucket) -> None:
        bucket.put(osv("A-1", "foo", modified="2026-11-10T10:00:00Z"))
        bucket.put(osv("A-2", "bar", modified="2026-11-10T11:00:00Z"))
        store = InMemoryAdvisoryStore()
        await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"])
        assert await store.lookup("pypi", ["foo"]) and await store.lookup("pypi", ["bar"])
        bucket.put(osv("A-1", "foo", modified="2026-11-12T10:00:00Z", withdrawn="2026-11-12T10:00:00Z"))
        bucket.docs["PyPI"]["A-2"] = {"id": "A-2", "modified": "2026-11-13T10:00:00Z"}
        bucket.hidden.add("A-2")  # still in the index, its file is gone
        out = await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"])
        assert out["ecosystems"]["pypi"]["removed"] == 2
        assert await store.lookup("pypi", ["foo"]) == [] and await store.lookup("pypi", ["bar"]) == []

    async def test_malicious_package_reports_are_skipped_unless_asked_for(self, bucket) -> None:
        bucket.put(osv("MAL-2026-1", "evilpkg", eco="npm"), "npm")
        bucket.put(osv("GHSA-1", "left-pad", eco="npm"), "npm")
        skipped, wanted = InMemoryAdvisoryStore(), InMemoryAdvisoryStore()
        out = await run_osv_sync(skipped, bucket.feed(), ecosystems=["npm"])
        assert out["ecosystems"]["npm"]["skipped_malware"] == 1 and await skipped.lookup("npm", ["evilpkg"]) == []
        await run_osv_sync(wanted, bucket.feed(), ecosystems=["npm"], include_malware=True)
        assert await wanted.lookup("npm", ["evilpkg"])

    async def test_a_failing_ecosystem_is_recorded_and_the_other_still_syncs(self, bucket) -> None:
        bucket.put(osv("A-1", "foo"))
        bucket.fail.add("npm")
        store = InMemoryAdvisoryStore()
        out = await run_osv_sync(store, bucket.feed(), ecosystems=["npm", "pypi"])
        assert out["status"] == "partial" and out["ecosystems"]["pypi"]["status"] == "success"
        by = {s.ecosystem: s for s in await store.status()}
        assert by["npm"].synced_at is None and "FeedError" in (by["npm"].last_error or "")
        assert by["pypi"].synced_at is not None and by["pypi"].last_error is None

    async def test_a_failure_after_a_good_run_keeps_the_old_sync_time(self, bucket) -> None:
        bucket.put(osv("A-1", "foo"))
        store, clock = InMemoryAdvisoryStore(), Clock()
        await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], clock=clock)
        bucket.fail.add("PyPI")
        clock.now = T0 + timedelta(days=2)
        out = await run_osv_sync(store, bucket.feed(), ecosystems=["pypi"], clock=clock)
        assert out["status"] == "error"
        (row,) = await store.status()
        assert row.synced_at == T0 and row.last_attempt_at == clock.now and row.last_error

    async def test_an_ecosystem_the_scan_cannot_match_is_skipped_not_fetched(self, bucket) -> None:
        out = await run_osv_sync(InMemoryAdvisoryStore(), bucket.feed(), ecosystems=["rubygems"])
        assert out["ecosystems"]["rubygems"]["status"] == "skipped" and bucket.hits == []
