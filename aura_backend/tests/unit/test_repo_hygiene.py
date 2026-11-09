"""Repository and CI hygiene the code review cannot see (audit F-37 / F-29, CB-13, CB-30).

Two things that kept regressing because nothing failed when they did:

* **Tracked build artefacts.** A 72 MB APK, a 7.3 MB Windows daemon binary with no extension, logs
  and scratch files were all committed despite ``.gitignore`` rules, because an ignore rule does
  not untrack a file already in the index. ``scripts/check_tracked_artifacts.py`` checks the index
  itself; this runs it as part of the default suite, and ``repo-hygiene.yml`` runs it on every PR
  whatever paths it touches (Backend CI's path filter would miss an APK added under aura_client/).
* **Floating CI toolchains.** ``release-apk.yml`` built releases on ``channel: stable`` while
  Client CI tested 3.44.6, and every action was referenced by a movable tag. The checks below pin
  both: third-party actions by full commit SHA, Flutter and Dart by exact version.

Needs only the checkout and ``git``: no Docker, no network.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPT = REPO / "scripts" / "check_tracked_artifacts.py"

# The Flutter release client-ci.yml's own comment records as verified against the pubspec, and
# the Dart SDK it ships. Change all of them together.
FLUTTER_PIN = "3.44.6"
DART_PIN = "3.12.2"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_tracked_artifacts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_git_checkout() -> bool:
    if shutil.which("git") is None:
        return False
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO,
                           capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def _workflows() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflows found under {WORKFLOWS}"
    return files


class TestTrackedArtifacts:
    @pytest.mark.skipif(not _in_git_checkout(), reason="not a git checkout (e.g. a source tarball)")
    def test_no_build_artefacts_are_tracked(self) -> None:
        checker = _load_checker()
        tracked = checker.tracked_files(REPO)
        problems = checker.find_violations(REPO, tracked)
        stale = sorted(set(checker.ALLOWLIST) - set(tracked))
        assert not problems, "tracked build artefacts / binaries:\n  " + "\n  ".join(problems)
        assert not stale, f"ALLOWLIST entries no longer tracked, remove them: {stale}"

    def test_checker_flags_each_kind_of_artefact(self, tmp_path: Path) -> None:
        # The repo-level test passes vacuously on a clean tree; this proves each check bites.
        checker = _load_checker()
        (tmp_path / "app.apk").write_bytes(b"PK\x03\x04")
        (tmp_path / "daemon.log").write_text("started\n")
        (tmp_path / "local.db").write_bytes(b"SQLite format 3\x00")
        (tmp_path / "aura_hostd").write_bytes(b"MZ\x90\x00" + b"\x00" * 64)  # PE, no extension
        (tmp_path / "linux_daemon").write_bytes(b"\x7fELF" + b"\x00" * 64)
        (tmp_path / "huge.bin.txt").write_bytes(b"x" * (checker.MAX_BYTES + 1))
        (tmp_path / "fine.py").write_text("print('ok')\n")
        names = ["app.apk", "daemon.log", "local.db", "aura_hostd", "linux_daemon", "huge.bin.txt",
                 "fine.py"]
        flagged = {p.split(":", 1)[0] for p in checker.find_violations(tmp_path, names)}
        assert flagged == set(names) - {"fine.py"}

    def test_checker_honours_its_allowlist(self, tmp_path: Path, monkeypatch) -> None:
        """A synthetic entry: the real allowlist is empty on purpose (FSA-113) and stays so unless
        someone defends a new entry."""
        checker = _load_checker()
        allowed = "deploy/kept-on-purpose.log"
        monkeypatch.setattr(checker, "ALLOWLIST", {allowed: "a test entry"})
        target = tmp_path / allowed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("a log kept by decision\n")
        assert checker.find_violations(tmp_path, [allowed]) == []
        assert checker.find_violations(tmp_path, ["deploy/other.log"]) != []

    def test_gitignore_covers_the_artefacts_that_were_committed(self) -> None:
        rules = {line.strip() for line in (REPO / ".gitignore").read_text().splitlines()}
        for rule in ("*.apk", "*.exe", "*.log", "*.db", "aura_client/releases/",
                     "aura_hostd/aura_hostd", "aura_hostd/build/"):
            assert rule in rules, f".gitignore lost {rule!r}"


_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)\s*(#.*)?$")


class TestWorkflowPins:
    def test_third_party_actions_are_pinned_to_a_commit_sha(self) -> None:
        offenders: list[str] = []
        for wf in _workflows():
            for lineno, line in enumerate(wf.read_text().splitlines(), 1):
                m = _USES.match(line)
                if not m:
                    continue
                ref = m.group(1)
                if ref.startswith(("./", "docker://")):
                    continue  # local action or a container image, not a tag-addressed action
                _, _, version = ref.partition("@")
                if not re.fullmatch(r"[0-9a-f]{40}", version):
                    offenders.append(f"{wf.name}:{lineno}: {ref} (pin to a full commit SHA)")
                elif not (m.group(2) or "").lstrip("# ").startswith("v"):
                    offenders.append(f"{wf.name}:{lineno}: {ref} (add a `# vX.Y.Z` comment)")
        assert not offenders, "\n".join(offenders)

    def test_flutter_is_pinned_to_one_version_everywhere(self) -> None:
        offenders: list[str] = []
        for wf in _workflows():
            lines = wf.read_text().splitlines()
            for i, line in enumerate(lines):
                if "subosito/flutter-action@" not in line:
                    continue
                block = []
                for nxt in lines[i + 1:i + 8]:
                    if re.match(r"^\s*- ", nxt):
                        break
                    block.append(nxt)
                text = "\n".join(block)
                if f'flutter-version: "{FLUTTER_PIN}"' not in text or "channel:" in text:
                    offenders.append(f"{wf.name}:{i + 1}: Flutter not pinned to {FLUTTER_PIN}")
        assert not offenders, "\n".join(offenders)

    def test_dart_sdk_is_pinned_to_the_flutter_release_dart(self) -> None:
        offenders: list[str] = []
        for wf in _workflows():
            lines = wf.read_text().splitlines()
            for i, line in enumerate(lines):
                if "dart-lang/setup-dart@" not in line:
                    continue
                block = "\n".join(lines[i + 1:i + 4])
                if f'sdk: "{DART_PIN}"' not in block:
                    offenders.append(f"{wf.name}:{i + 1}: setup-dart not pinned to {DART_PIN}")
        assert not offenders, "\n".join(offenders)

    def test_admin_ci_runs_on_pull_requests(self) -> None:
        text = (WORKFLOWS / "admin-ci.yml").read_text()
        on_block = text.split("\non:\n", 1)[1].split("\njobs:\n", 1)[0]
        assert re.search(r"^  pull_request:", on_block, re.M), "admin-ci.yml must run on pull_request"
        assert '"aura_admin/**"' in on_block
        # Audit G-10: the console has an ESLint config now (eslint.config.mjs) and lint is a gate.
        assert re.search(r"^\s*run:\s*npm run lint", text, re.M), "admin-ci.yml must run the lint"
        assert (REPO / "aura_admin" / "eslint.config.mjs").is_file()
        package = json.loads((REPO / "aura_admin" / "package.json").read_text(encoding="utf-8"))
        assert "--max-warnings 0" in package["scripts"]["lint"], "a warning must fail the gate"


class TestTheIndexCarriesNoScratchFiles:
    """Audit FSA-113: `deploy/vps_known_hosts.tmp` duplicated the stale host-key pin and was carried
    by an allowlist entry rather than deleted. A `.tmp` is scratch by definition; the guard's
    allowlist is for files that must stay tracked, never for a scratch file nobody decided on."""

    @pytest.mark.skipif(not _in_git_checkout(), reason="not a git checkout (e.g. a source tarball)")
    def test_no_tracked_file_is_a_tmp_file_allowlisted_or_not(self) -> None:
        checker = _load_checker()
        tracked = checker.tracked_files(REPO)
        tmp = [p for p in tracked if p.lower().endswith(".tmp")]
        assert tmp == [], f"scratch files in the index: {tmp}"

    @pytest.mark.skipif(not _in_git_checkout(), reason="not a git checkout (e.g. a source tarball)")
    def test_every_allowlist_entry_is_a_tracked_non_scratch_file(self) -> None:
        checker = _load_checker()
        tracked = set(checker.tracked_files(REPO))
        for rel in checker.ALLOWLIST:
            assert rel in tracked, f"stale allowlist entry, file is not tracked: {rel}"
            assert not rel.lower().endswith(".tmp"), f"a .tmp file may not be allowlisted: {rel}"


class TestDevelopmentToolingIsHashPinned:
    """Audit G-06 / GAP-033: the runtime lock is hash-pinned, the test tooling was `pytest>=8.0` --
    4,000 tests ran under whatever pytest resolved that day, and a compromised wheel on PyPI would
    have been installed on every CI run. `requirements-dev.lock` closes that, and every workflow
    that installs the tooling must install the lock with hashes checked, never the loose file."""

    DEV_LOCK = REPO / "aura_backend" / "requirements-dev.lock"

    def test_the_dev_lock_exists_with_a_hash_for_every_pin(self) -> None:
        assert self.DEV_LOCK.is_file(), "aura_backend/requirements-dev.lock is missing"
        text = self.DEV_LOCK.read_text(encoding="utf-8")
        assert "uv pip compile" in text.splitlines()[1], "the lock must record the command that made it"
        pins = re.findall(r"^([A-Za-z0-9_.-]+)==", text, re.M)
        assert "pytest" in pins and "testcontainers" in pins
        assert text.count("--hash=sha256:") >= len(pins), "every pin needs at least one hash"

    def test_the_dev_lock_is_constrained_by_the_runtime_lock(self) -> None:
        """A package pinned in both locks must be pinned to the same version, or the second
        install would silently replace what the first hash-checked."""
        runtime = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^ \\\n]+)", (REPO / "aura_backend" / "requirements.lock").read_text(encoding="utf-8"), re.M))
        dev = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^ \\\n]+)", self.DEV_LOCK.read_text(encoding="utf-8"), re.M))
        clashes = {name: (runtime[name], dev[name]) for name in dev if name in runtime and runtime[name] != dev[name]}
        assert clashes == {}, f"dev lock disagrees with the runtime lock: {clashes}"

    def test_every_workflow_installs_the_dev_lock_with_hashes_checked(self) -> None:
        installers = []
        for path in _workflows():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "requirements-dev" in line and "pip install" in line:
                    installers.append((path.name, line.strip()))
        assert installers, "no workflow installs the development tooling"
        loose = [(n, l) for n, l in installers if "requirements-dev.txt" in l]
        assert loose == [], f"workflows still install the unhashed requirements-dev.txt: {loose}"
        unhashed = [(n, l) for n, l in installers if "--require-hashes" not in l]
        assert unhashed == [], f"dev lock installed without --require-hashes: {unhashed}"


class TestWindowsJobsGate:
    """Audit G-03 / FSA-063: the desktop targets are Windows-first, and the Windows jobs were the
    only ones allowed to fail silently. Both have been seen green on real runners (client #213-#215,
    hostd on #221), so neither may stay advisory."""

    @pytest.mark.parametrize("workflow", ["client-ci.yml", "hostd-ci.yml"])
    def test_no_job_may_fail_without_failing_the_run(self, workflow: str) -> None:
        text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
        assert not re.search(r"^\s*continue-on-error:\s*true", text, re.M), (
            f"{workflow} still has an advisory job; a red Windows job must fail the run"
        )


class TestTheLegacyTreeIsMeasuredAndItsPostgresTestsRun:
    """Audit G-05: the coverage ratchet measured `aura_core` only, and the legacy tree's seven
    Postgres-backed integration suites (`aura_backend/test_*_integration.py`) ran only where a
    Docker daemon happened to be -- never in the `postgres` job that already has a real server."""

    BACKEND_CI = WORKFLOWS / "backend-ci.yml"

    def test_the_legacy_suite_runs_under_coverage_for_the_platform_packages(self) -> None:
        text = self.BACKEND_CI.read_text(encoding="utf-8")
        legacy = text.split("name: Legacy suite (unittest)", 1)[1].split("\n      - name:", 1)[0]
        assert "coverage run" in legacy, "the legacy step must run under coverage (no floor yet)"
        for package in ("api", "services", "infrastructure"):
            assert package in legacy, f"legacy coverage must include {package}/"
        assert "coverage report" in legacy

    def test_the_postgres_job_runs_the_legacy_integration_suites_against_its_service(self) -> None:
        text = self.BACKEND_CI.read_text(encoding="utf-8")
        postgres_job = text.split("\n  postgres:\n", 1)[1].split("\n  docker-build:\n", 1)[0]
        assert 'test_*_integration.py' in postgres_job, (
            "the postgres job must discover aura_backend/test_*_integration.py"
        )
        assert "AURA_TEST_DATABASE_URL" in postgres_job


class TestEngineBackedVoiceAndOcrTestsRunInCi:
    """Audit FSA-081: every real-engine speech and OCR test is skipif-gated on a binary, and CI
    installed neither, so none of them had ever run anywhere but a contributor's machine."""

    def test_backend_ci_installs_the_speech_and_ocr_engines(self) -> None:
        text = (WORKFLOWS / "backend-ci.yml").read_text(encoding="utf-8")
        test_job = text.split("\n  test:\n", 1)[1].split("\n  pip-audit:\n", 1)[0]
        for package in ("espeak-ng", "tesseract-ocr"):
            assert re.search(rf"apt-get install[^\n]*\b{re.escape(package)}\b", test_job), (
                f"the test job must apt-get install {package}"
            )


class TestARealModelEvaluationExists:
    """Audit FSA-082 / G-02 / G-09: every AI-quality number in the repository came from the hash
    embedder and scripted providers; the deterministic gate is right as a merge gate and useless as
    a measurement. A separate workflow runs the agent suite and the vision evaluator against a real
    provider and the production embedder, on a schedule and on demand, and skips with a notice,
    not a pass, when the credentials it needs are absent."""

    WORKFLOW = WORKFLOWS / "real-model-eval.yml"

    def test_the_workflow_exists_and_is_both_scheduled_and_dispatchable(self) -> None:
        assert self.WORKFLOW.is_file(), "no real-model evaluation workflow"
        text = self.WORKFLOW.read_text(encoding="utf-8")
        on_block = text.split("\non:\n", 1)[1].split("\njobs:\n", 1)[0]
        assert re.search(r"^  workflow_dispatch:", on_block, re.M)
        assert re.search(r"^  schedule:", on_block, re.M)

    def test_it_runs_the_real_embedder_not_the_hash_one(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        assert "AURA_EMBEDDING_PROVIDER: sentence-transformers" in text
        assert "hash-embed" not in text

    def test_it_runs_the_evaluator_against_a_hosted_provider_with_the_key_from_a_secret(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        assert "python -m evaluation.runner" in text
        assert "python -m evaluation.vision_eval run" in text
        assert "AURA_ALLOW_HOSTED_INFERENCE: \"true\"" in text
        assert "secrets.AURA_HOSTED1_API_KEY" in text
        assert "--baseline" not in text.split("evaluation.runner", 1)[1].split("\n", 2)[0], (
            "the scripted baselines are tautological; a real-model run reports, it does not gate"
        )

    def test_missing_credentials_skip_with_a_notice_instead_of_passing(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        assert "::notice::" in text and "configured" in text
