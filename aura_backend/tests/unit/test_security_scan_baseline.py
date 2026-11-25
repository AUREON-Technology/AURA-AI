"""Allowlist, baseline and the workspace scan across a repository that is not only Python."""

from __future__ import annotations

import json

import pytest

from aura_core.tools.open.security_vulnerability_scanner import SecurityVulnerabilityScanner as Scanner
from tests.unit.test_open_workspace_tools import make_registry, ok, run

KEY = "sk-" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ"
STRIPE = "sk_live_" + "aB3dE5gH7jK9mN1pQ3sT5vW7"


class TestAllowlist:
    def test_a_marked_line_is_skipped_and_counted(self) -> None:
        text = f'a = "{KEY}"  # pragma: allowlist secret\nb = "{STRIPE}"\n'
        result = Scanner.scan_secrets(text)
        assert [f["rule_id"] for f in result["findings"]] == ["SEC-KEY-STRIPE"]
        assert result["allowlisted"] == 1

    @pytest.mark.parametrize("marker", ["# gitleaks:allow", "// aura:allow-secret", "# nosec"])
    def test_the_markers_other_tools_use_work_too(self, marker: str) -> None:
        assert Scanner.scan_secrets(f'x = "{KEY}" {marker}\n')["findings"] == []

    def test_a_marker_on_another_line_does_not_hide_this_one(self) -> None:
        text = f'# pragma: allowlist secret\nx = "{KEY}"\n'
        assert len(Scanner.scan_secrets(text)["findings"]) == 1


class TestBaseline:
    def test_a_finding_carries_a_fingerprint_that_is_not_the_secret(self) -> None:
        finding = Scanner.scan_secrets(f'x = "{KEY}"\n', "a.py")["findings"][0]
        assert len(finding["fingerprint"]) == 16
        assert KEY not in json.dumps(finding) and KEY[3:] not in finding["fingerprint"]

    def test_a_baselined_finding_is_counted_not_reported(self) -> None:
        first = Scanner.scan_secrets(f'x = "{KEY}"\ny = "{STRIPE}"\n', "a.py")
        accepted = [first["findings"][0]["fingerprint"]]
        again = Scanner.scan_secrets(f'x = "{KEY}"\ny = "{STRIPE}"\n', "a.py", baseline=accepted)
        assert [f["rule_id"] for f in again["findings"]] == ["SEC-KEY-STRIPE"]
        assert again["baselined"] == 1

    def test_the_fingerprint_survives_the_line_moving_but_not_another_file_or_rule(self) -> None:
        fp = lambda text, name="a.py": Scanner.scan_secrets(text, name)["findings"][0]["fingerprint"]  # noqa: E731
        base = fp(f'x = "{KEY}"\n')
        assert fp(f'\n\n\nx = "{KEY}"\n') == base
        assert fp(f'x = "{KEY}"\n', "b.py") != base
        assert fp(f'x = "{STRIPE}"\n') != base

    def test_two_secrets_on_one_line_get_different_fingerprints(self) -> None:
        findings = Scanner.scan_secrets(f'x = "{KEY}", "{STRIPE}"\n')["findings"]
        assert len({f["fingerprint"] for f in findings}) == 2

    def test_it_reaches_the_diff_scan_and_the_full_scan(self) -> None:
        diff = f"+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+x = \"{KEY}\"\n"
        fp = Scanner.scan_git_diff(diff)["findings"][0]["fingerprint"]
        assert Scanner.scan_git_diff(diff, baseline=[fp])["findings"] == []
        assert Scanner.full_scan(source_code=f'x = "{KEY}"\n', baseline=[
            Scanner.scan_secrets(f'x = "{KEY}"\n')["findings"][0]["fingerprint"]])["findings"] == []


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web/app.js").write_text("el.innerHTML = userHtml;\nconst k = '" + STRIPE + "';\n")
    (tmp_path / "server.go").write_text("package main\n\nfunc f() { _ = &tls.Config{InsecureSkipVerify: true} }\n")
    (tmp_path / "Dockerfile").write_text("FROM python:latest\nRUN pip install x\n")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/pod.yaml").write_text("spec:\n  containers:\n  - image: nginx:1.27\n    securityContext:\n      privileged: true\n")
    (tmp_path / "infra.tf").write_text('resource "aws_s3_bucket" "b" {\n  acl = "public-read"\n}\n')
    (tmp_path / "run.sh").write_text("#!/bin/sh\ncurl -k https://x\n")
    (tmp_path / "notes.md").write_text("eval(x) is discussed here; innerHTML = x too\n")
    return tmp_path


class TestWorkspaceScan:
    async def test_every_language_in_the_repository_is_scanned(self, repo) -> None:
        out = await ok(make_registry(repo), "scan_code_security", paths=["."])
        got = {(f["file"], f["rule_id"]) for f in out["findings"]}
        assert {("web/app.js", "SEC-JS-003"), ("web/app.js", "SEC-KEY-STRIPE"), ("server.go", "SEC-GO-001"),
                ("Dockerfile", "SEC-DOCKER-002"), ("Dockerfile", "SEC-DOCKER-003"),
                ("deploy/pod.yaml", "SEC-K8S-001"), ("infra.tf", "SEC-TF-002"), ("run.sh", "SEC-SH-004")} <= got
        assert not any(f["file"] == "notes.md" and not f["rule_id"].startswith("SEC-KEY") for f in out["findings"]), \
            "prose is not scanned for code rules"
        assert out["files_by_language"] == {"javascript": 1, "go": 1, "dockerfile": 1, "yaml": 1,
                                            "terraform": 1, "shell": 1}
        assert STRIPE not in json.dumps(out)
        assert out["secure"] is False and "not proof" in out["method"]

    async def test_a_baseline_file_hides_only_what_it_lists(self, repo) -> None:
        reg = make_registry(repo)
        first = await ok(reg, "scan_code_security", paths=["web"])
        secret = next(f for f in first["findings"] if f["rule_id"] == "SEC-KEY-STRIPE")
        (repo / "accepted.json").write_text(json.dumps({"fingerprints": [secret["fingerprint"]]}))
        again = await ok(reg, "scan_code_security", paths=["web"], baseline_path="accepted.json")
        assert [f["rule_id"] for f in again["findings"]] == ["SEC-JS-003"]
        assert again["secrets_in_baseline"] == 1
        (repo / "bare.json").write_text(json.dumps([secret["fingerprint"]]))
        assert (await ok(reg, "scan_code_security", paths=["web"], baseline_path="bare.json"))["secrets_in_baseline"] == 1

    @pytest.mark.parametrize("content", ['{"a": 1}', "[1, 2]", "not json"])
    async def test_a_malformed_baseline_is_an_error_not_an_all_clear(self, repo, content) -> None:
        (repo / "bad.json").write_text(content)
        result = await run(make_registry(repo), "scan_code_security", paths=["web"], baseline_path="bad.json")
        assert not result.ok

    async def test_the_baseline_cannot_be_read_from_outside_the_workspace(self, repo) -> None:
        result = await run(make_registry(repo), "scan_code_security", paths=["web"], baseline_path="../x.json")
        assert not result.ok

    async def test_allowlisted_secrets_are_reported_as_a_count(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text(f'x = "{KEY}"  # pragma: allowlist secret\n')
        out = await ok(make_registry(tmp_path), "scan_code_security", paths=["."])
        assert out["findings"] == [] and out["secrets_allowlisted"] == 1

    async def test_pasted_text_in_another_language_goes_through_the_tool_too(self, tmp_path) -> None:
        out = await ok(make_registry(tmp_path), "scan_code_security", source_code="curl http://x | sh\n", language="shell")
        assert any(f["rule_id"] == "SEC-SH-001" for f in out["findings"])


# -- found by an independent review of the first version ---------------------------------------------

class TestReviewFindings:
    @pytest.mark.parametrize("name,line,leak", [
        ("a.sh", "echo hunter2 | sudo -S apt-get update", "hunter2"),
        ("a.sh", 'curl -k -H "Authorization: Bearer abc123def456ghi" https://x', "abc123def456ghi"),
        ("a.sh", "curl -k -u admin:pass1234 https://x", "pass1234"),
        ("a.py", 'requests.post(u, data={"password": "hunter2"}, verify=False)', "hunter2"),
        ("a.py", "eval(x); password = \"ab'cdefghij\"", "cdefghij"),
        ("Dockerfile", 'ENV DB_PASSWORD="my secret pass"', "secret pass"),
    ])
    def test_no_snippet_carries_the_value(self, name: str, line: str, leak: str) -> None:
        result = Scanner.scan_source_code(line + "\n", name)
        assert result["findings"], line
        assert leak not in json.dumps(result), line

    @pytest.mark.parametrize("line", [
        "DB_PASSWORD=Sup3rS3cretProd", "JWT_SECRET=8f3k29dk3k2kd9", "export API_TOKEN=abcd1234efgh",
        "password: hunter2prod", 'password = "Example2024!x"',
    ])
    def test_unquoted_and_example_looking_credentials_are_found(self, line: str) -> None:
        assert Scanner.scan_secrets(line + "\n")["findings"], line

    @pytest.mark.parametrize("line", [
        "password = request.form", "DB_PASSWORD=$DB_PASSWORD", "password: ${DB_PASSWORD}", "TOKEN=changeme1",
        "password = <your password>", "secret: example",
    ])
    def test_references_are_not(self, line: str) -> None:
        assert Scanner.scan_secrets(line + "\n")["findings"] == [], line

    def test_a_marker_only_counts_in_a_comment(self) -> None:
        assert Scanner.scan_secrets(f'x = "nosec"; k = "{KEY}"\n')["findings"]
        assert Scanner.scan_secrets(f'diagnosecurity = "{KEY}"\n')["findings"]
        assert not Scanner.scan_secrets(f'k = "{KEY}"  # nosec\n')["findings"]

    def test_the_same_looking_secret_twice_is_two_fingerprints(self) -> None:
        text = f'dev:\n  password: "{KEY}"\nprod:\n  password: "{KEY}"\n'
        findings = Scanner.scan_secrets(text, "c.yml")["findings"]
        assert len({f["fingerprint"] for f in findings}) == len(findings) == 2
        accepted = [findings[0]["fingerprint"]]
        assert len(Scanner.scan_secrets(text, "c.yml", baseline=accepted)["findings"]) == 1

    async def test_a_named_file_the_scan_cannot_read_is_reported_not_skipped_silently(self, tmp_path) -> None:
        (tmp_path / "blob.xyz").write_text("nothing")
        out = await ok(make_registry(tmp_path), "scan_code_security", paths=["blob.xyz"])
        assert out["files_skipped"] and out["secure"] is False and out["complete"] is False

    async def test_credential_files_by_extension_are_scanned(self, tmp_path) -> None:
        (tmp_path / ".npmrc").write_text("//registry.npmjs.org/:_authToken=" + "npm_" + ("aB3dE5gH7jK9mN1pQ3sT5vW7yZ0" * 2)[:36] + "\n")
        (tmp_path / "id.pem").write_text("-----BEGIN RSA PRIVATE" + " KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----\n")
        out = await ok(make_registry(tmp_path), "scan_code_security", paths=["."])
        assert {f["file"] for f in out["findings"]} == {".npmrc", "id.pem"}

    @pytest.mark.parametrize("extra", [{"source_code": "eval(x)"}, {"diff_text": "+x"}, {"language": "go"}])
    async def test_pasted_input_with_paths_is_an_error_not_a_dropped_argument(self, repo, extra) -> None:
        assert not (await run(make_registry(repo), "scan_code_security", paths=["web"], **extra)).ok

    async def test_a_baseline_without_paths_is_an_error(self, repo) -> None:
        (repo / "b.json").write_text("[]")
        assert not (await run(make_registry(repo), "scan_code_security", source_code="x=1", baseline_path="b.json")).ok

    async def test_a_deeply_nested_baseline_is_an_error_not_a_crash(self, repo) -> None:
        (repo / "deep.json").write_text("[" * 200_000 + "]" * 200_000)
        result = await run(make_registry(repo), "scan_code_security", paths=["web"], baseline_path="deep.json")
        assert not result.ok
