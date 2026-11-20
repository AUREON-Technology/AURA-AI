"""Regression tests for the bugs found when the ``tools/open`` classes were reviewed for exposure.

Each test reproduces the failure as it was found, so it fails on the unfixed code.
"""

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aura_core.tools.open._process import PYTEST_ENV, run_bounded, scrubbed_env
from aura_core.tools.open.automated_test_generator_and_verifier import AutomatedTestGeneratorAndVerifier
from aura_core.tools.open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner
from aura_core.tools.open.model_drift_and_performance_monitor import ModelDriftAndPerformanceMonitor
from aura_core.tools.open.performance_profiler import PerformanceProfiler
from aura_core.tools.open.quality_gate_aggregator import QualityGateAggregator
from aura_core.tools.open.regression_impact_analyzer import RegressionImpactAnalyzer
from aura_core.tools.open.security_vulnerability_scanner import SecurityVulnerabilityScanner
from aura_core.tools.open.test_coverage_and_mutation_analyzer import TestCoverageAndMutationAnalyzer


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


class TestGitChangedFiles:
    def test_reports_files_changed_since_the_ref(self, tmp_path: Path) -> None:
        """`git diff --name-only -- HEAD` treats HEAD as a pathspec and is always empty."""
        _git(tmp_path, "init", "-q")
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "init")
        (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

        result = RegressionImpactAnalyzer.get_git_changed_files(str(tmp_path), "HEAD")

        assert result["success"] is True
        assert result["files"] == ["a.py"]

    @pytest.mark.parametrize("ref", ["--output=/tmp/x", "-p", "HEAD;rm"])
    def test_option_like_refs_are_still_refused(self, tmp_path: Path, ref: str) -> None:
        assert RegressionImpactAnalyzer.get_git_changed_files(str(tmp_path), ref)["success"] is False


class TestLicenseScanner:
    @staticmethod
    def _scan(license_text: str, **kwargs):
        return DependencyLicenseComplianceScanner.scan_licenses(
            [{"package": "p", "license": license_text}], **kwargs
        )

    @pytest.mark.parametrize("lic", ["GPL-3.0", "GPL-3.0-only", "GPL-3.0+", "GPLv3", "AGPL-3.0-or-later", "SSPL-1.0", "BUSL-1.1"])
    def test_strong_copyleft_is_flagged(self, lic: str) -> None:
        assert self._scan(lic)["compliant"] is False

    @pytest.mark.parametrize("lic", ["LGPL-3.0", "LGPL-2.1-or-later", "BSL-1.0", "MIT", "Apache-2.0", "BSD-3-Clause"])
    def test_weak_copyleft_and_permissive_are_not_substring_matches(self, lic: str) -> None:
        """`LGPL-3.0` contains `GPL-3.0` and `BSL-1.0` (Boost) started with `BSL`."""
        assert self._scan(lic)["compliant"] is True

    @pytest.mark.parametrize("lic", ["UNKNOWN", "", "NOASSERTION"])
    def test_unknown_licenses_fail_instead_of_passing_silently(self, lic: str) -> None:
        result = self._scan(lic)
        assert result["compliant"] is False
        assert result["violations"][0]["unknown_license"] is True

    def test_an_or_expression_is_compliant_when_one_branch_is(self) -> None:
        assert self._scan("GPL-3.0-only OR MIT")["compliant"] is True
        assert self._scan("GPL-3.0-only AND MIT")["compliant"] is False

    def test_the_allowlist_applies_to_every_and_component(self) -> None:
        assert self._scan("MIT AND Apache-2.0", allowed_licenses=["MIT", "Apache-2.0"])["compliant"] is True
        assert self._scan("MIT AND ISC", allowed_licenses=["MIT"])["compliant"] is False

    def test_a_missing_license_key_is_unknown(self) -> None:
        result = DependencyLicenseComplianceScanner.scan_licenses([{"package": "p"}])
        assert result["compliant"] is False


class TestQualityGateFailsClosed:
    def test_a_gate_with_nothing_run_is_not_approved(self) -> None:
        result = QualityGateAggregator.evaluate_checks()
        assert result["status"] == "FAILED"
        assert set(result["failed_checks"]) == {"security", "tests", "coverage", "complexity"}
        assert "lint" in result["not_run"]  # optional: skipped, not failed

    def test_all_required_checks_passing_is_approved(self) -> None:
        result = QualityGateAggregator.evaluate_checks(
            security_pass=True, tests_pass=True, coverage_pass=True, complexity_pass=True
        )
        assert result["status"] == "APPROVED"

    def test_an_explicit_false_still_fails(self) -> None:
        result = QualityGateAggregator.evaluate_checks(
            security_pass=True, tests_pass=False, coverage_pass=True, complexity_pass=True
        )
        assert result["failed_checks"] == ["tests"]

    def test_an_unknown_blocking_check_is_rejected_not_treated_as_passing(self) -> None:
        with pytest.raises(ValueError, match="Unknown check"):
            QualityGateAggregator.build_release_readiness_report(
                security_pass=True, tests_pass=True, coverage_pass=True, complexity_pass=True,
                blocking_checks=["securty"],
            )

    def test_a_blocker_that_was_not_run_blocks_the_release(self) -> None:
        report = QualityGateAggregator.build_release_readiness_report(
            tests_pass=True, coverage_pass=True, complexity_pass=True
        )
        assert report["ready_to_merge"] is False
        assert "security" in report["blocker_failures"]

    def test_a_clean_report_is_ready(self) -> None:
        report = QualityGateAggregator.build_release_readiness_report(
            security_pass=True, tests_pass=True, coverage_pass=True, complexity_pass=True
        )
        assert report["ready_to_merge"] is True
        assert report["quality_score"] == 100.0


class TestDriftMonitorFailsClosed:
    @pytest.mark.parametrize("current", [float("nan"), float("inf")])
    def test_non_finite_accuracy_is_drift_not_silence(self, current: float) -> None:
        result = ModelDriftAndPerformanceMonitor.check_accuracy_drift(0.9, current)
        assert result["drift_detected"] is True
        assert result["status"] == "invalid_input"

    def test_empty_samples_are_drift_not_silence(self) -> None:
        result = ModelDriftAndPerformanceMonitor.check_mean_shift([], [1.0])
        assert result["drift_detected"] is True
        assert "non-empty" in result["error"]

    def test_nan_in_a_sample_is_invalid(self) -> None:
        assert ModelDriftAndPerformanceMonitor.check_mean_shift([1.0, math.nan], [1.0])["status"] == "invalid_input"

    def test_a_real_drop_is_still_detected_and_a_small_one_is_not(self) -> None:
        assert ModelDriftAndPerformanceMonitor.check_accuracy_drift(0.9, 0.7)["drift_detected"] is True
        ok = ModelDriftAndPerformanceMonitor.check_accuracy_drift(0.9, 0.88)
        assert ok["drift_detected"] is False and ok["status"] == "ok"


class TestPerformanceProfiler:
    def test_a_code_string_is_refused_not_evaluated(self, tmp_path: Path) -> None:
        marker = tmp_path / "pwned"
        payload = f"open({str(marker)!r}, 'w').write('x')"
        result = PerformanceProfiler.profile_callable(payload)
        assert result["success"] is False
        assert "callable" in result["error"]
        assert not marker.exists()

    def test_total_is_the_sum_not_the_mean(self) -> None:
        result = PerformanceProfiler.benchmark_callable(lambda: None, iterations=4)
        assert result["total_ms"] >= result["avg_ms"] * 4 - 0.01

    def test_a_coroutine_that_actually_awaits_completes(self) -> None:
        import asyncio

        async def slow(x):
            await asyncio.sleep(0.01)
            return x + 1

        assert PerformanceProfiler.profile_callable(slow, 1)["result"] == 2


class TestMutationAnalyzerBounds:
    def test_a_slow_baseline_is_a_result_not_an_exception(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text("FLAG = True\n", encoding="utf-8")
        (tmp_path / "test_mod.py").write_text(
            "import time\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8"
        )
        result = TestCoverageAndMutationAnalyzer.evaluate_mutations_against_tests(
            "mod.py", str(tmp_path), timeout_seconds=2
        )
        assert result["evaluated"] is False
        assert "timed out" in result["error"]

    def test_the_source_file_cannot_escape_the_working_dir(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        (tmp_path / "outside.py").write_text("FLAG = True\n", encoding="utf-8")
        result = TestCoverageAndMutationAnalyzer.evaluate_mutations_against_tests(
            "../outside.py", str(work)
        )
        assert result["evaluated"] is False
        assert "escapes" in result["error"]


class TestScrubbedChildEnvironment:
    def test_secrets_in_the_gateway_environment_do_not_reach_the_child(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "super-secret-value")
        monkeypatch.setenv("AURA_PROVIDER_HOSTED1_API_KEY", "sk-test-0123456789abcdef")
        assert "AURA_JWT_SECRET" not in scrubbed_env()
        proc = run_bounded(
            [sys.executable, "-c", "import os; print(sorted(k for k in os.environ if 'AURA' in k))"],
            cwd=None, timeout_s=30,
        )
        assert proc.stdout.strip() == "[]"

    def test_generated_tests_run_without_the_gateways_secrets(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_SECRET", "super-secret-value")
        test_code = (
            "import os\n\n"
            "def test_no_secret_visible():\n"
            "    assert 'AURA_JWT_SECRET' not in os.environ\n"
        )
        result = AutomatedTestGeneratorAndVerifier.run_tests_in_temp_dir(test_code, timeout_seconds=60)
        assert result["passed"] is True, result

    def test_pytest_children_do_not_autoload_third_party_plugins(self) -> None:
        assert PYTEST_ENV["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert scrubbed_env(PYTEST_ENV)["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert os.environ.get("PATH") == scrubbed_env().get("PATH")


class TestSecurityScannerSnippets:
    def test_a_secret_on_a_flagged_line_is_redacted_in_the_snippet(self) -> None:
        source = 'eval(x); password = "hunter2hunter2"\n'
        findings = SecurityVulnerabilityScanner.scan_source_code(source)["findings"]
        assert findings, "the eval() should be flagged"
        assert all("hunter2hunter2" not in f["snippet"] for f in findings)
