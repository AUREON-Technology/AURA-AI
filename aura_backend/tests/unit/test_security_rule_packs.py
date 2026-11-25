"""``scan_code_security``'s per-language rules: what each pack flags, what it leaves alone, and that
no credential ever appears in a result.

Fixtures are tiny real files of each kind. Every rule has a positive case; the lookalikes that must
*not* be flagged are as important, since a noisy scanner is one people switch off.
"""

from __future__ import annotations

import json
import time

import pytest

from aura_core.tools.open import security_rules as rules
from aura_core.tools.open.security_vulnerability_scanner import SecurityVulnerabilityScanner as Scanner


def ids(source: str, filename: str = "<string>", language: str | None = None) -> set[str]:
    return {f["rule_id"] for f in Scanner.scan_source_code(source, filename, language)["findings"]}


def found_at(source: str, rule_id: str, filename: str = "<string>") -> list[int]:
    return [f["line"] for f in Scanner.scan_source_code(source, filename)["findings"] if f["rule_id"] == rule_id]


# -- language detection ---------------------------------------------------------------------------


class TestDetectLanguage:
    @pytest.mark.parametrize("name,expected", [
        ("a.py", "python"), ("web/app.tsx", "javascript"), ("x.mjs", "javascript"), ("main.go", "go"),
        ("A.java", "java"), ("A.kt", "java"), ("run.sh", "shell"), ("Dockerfile", "dockerfile"),
        ("Dockerfile.prod", "dockerfile"), ("api.dockerfile", "dockerfile"), ("k8s/deploy.yaml", "yaml"),
        ("docker-compose.yml", "yaml"), ("main.tf", "terraform"), ("prod.tfvars", "terraform"),
    ])
    def test_from_the_file_name(self, name: str, expected: str) -> None:
        assert rules.detect_language("", name) == expected

    @pytest.mark.parametrize("source,expected", [
        ("#!/bin/bash\necho hi\n", "shell"),
        ("#!/usr/bin/env python3\nx = 1\n", "python"),
        ("#!/usr/bin/env node\nconsole.log(1)\n", "javascript"),
        ("def f(x):\n    return x\n", "python"),
        ("FROM python:3.12\nRUN pip install x\n", "dockerfile"),
        ('resource "aws_s3_bucket" "b" {\n  acl = "private"\n}\n', "terraform"),
        ("apiVersion: v1\nkind: Pod\n", "yaml"),
        ("package main\n\nfunc main() {}\n", "go"),
        ("public class A {\n  void f() {}\n}\n", "java"),
        ("const a = require('x');\nfunction f() {}\n", "javascript"),
    ])
    def test_from_the_text_when_there_is_no_name(self, source: str, expected: str) -> None:
        assert rules.detect_language(source) == expected

    def test_a_hint_wins_and_knows_common_names(self) -> None:
        assert rules.detect_language("def f(): pass", "a.py", "ts") == "javascript"
        assert rules.detect_language("", "", "k8s") == "yaml"
        assert rules.detect_language("", "", "golang") == "go"

    def test_nothing_to_go_on_is_none_and_the_scan_says_so(self) -> None:
        assert rules.detect_language("hello there") is None
        result = Scanner.scan_source_code("hello there\n")
        assert result["language"] == "unknown" and result["rules_applied"] == "python"

    def test_a_pdf_is_never_given_a_language_from_its_prose(self, tmp_path) -> None:
        text = "we use const x = eval(y) in the example"
        assert Scanner.scan_source_code(text, "paper.pdf", sniff=False)["language"] == "unknown"


# -- the packs ---------------------------------------------------------------------------------


class TestJavaScript:
    BAD = {
        "SEC-JS-001": "const r = eval(input);",
        "SEC-JS-002": "const f = new Function('a', body);",
        "SEC-JS-003": "el.innerHTML = userHtml;",
        "SEC-JS-004": "document.write(markup);",
        "SEC-JS-005": "<div dangerouslySetInnerHTML={{__html: html}} />",
        "SEC-JS-006": "const out = execSync(`ls ${dir}`);",
        "SEC-JS-007": "setTimeout('doIt()', 10);",
        "SEC-JS-008": "https.request({ rejectUnauthorized: false });",
        "SEC-JS-009": "crypto.createHash('md5').update(x);",
        "SEC-JS-010": "const token = Math.random().toString(36);",
        "SEC-JS-011": "db.query(`SELECT * FROM t WHERE id = ${id}`);",
        "SEC-JS-012": "res.setHeader('Access-Control-Allow-Origin', '*');",
        "SEC-JS-013": "vm.runInNewContext(code, sandbox);",
        "SEC-JS-014": "jwt.verify(t, k, { algorithms: ['none'] });",
        "SEC-JS-015": "localStorage.setItem('auth_token', t);",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "a.js")

    @pytest.mark.parametrize("line", [
        "const m = /a(b)/.exec(text);", "re.exec(s);", "model.eval();", "el.innerHTML = '';",
        "el.textContent = userText;", "exec('git status');", "const n = Math.random();",
        "db.query('SELECT 1 WHERE id = ?', [id]);", "// eval(x)", " * eval(x) in a doc comment",
        "setTimeout(() => go(), 10);", "createHash('sha256')",
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "a.ts") == set(), line

    def test_typescript_and_jsx_files_use_the_same_pack(self) -> None:
        assert "SEC-JS-001" in ids("eval(x)\n", "a.tsx") and "SEC-JS-001" in ids("eval(x)\n", "a.jsx")


class TestGo:
    BAD = {
        "SEC-GO-001": "tr := &tls.Config{InsecureSkipVerify: true}",
        "SEC-GO-002": 'exec.Command("sh", "-c", cmd).Run()',
        "SEC-GO-003": "h := md5.New()",
        "SEC-GO-004": 'rows, _ := db.Query("SELECT * FROM t WHERE id = " + id)',
        "SEC-GO-005": 'os.WriteFile(name, data, 0777)',
        "SEC-GO-006": "return template.HTML(userInput)",
        "SEC-GO-007": "cfg := &tls.Config{MinVersion: tls.VersionTLS10}",
        "SEC-GO-008": "token := rand.Intn(1000000)",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "a.go")

    @pytest.mark.parametrize("line", [
        'exec.Command("ls", "-l").Run()', 'db.Query("SELECT 1 WHERE id = $1", id)',
        "os.WriteFile(name, data, 0o600)", "h := sha256.New()", "// InsecureSkipVerify: true",
        "cfg := &tls.Config{MinVersion: tls.VersionTLS12}", "n := rand.Intn(10)",
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "a.go") == set(), line


class TestJava:
    BAD = {
        "SEC-JAVA-001": 'Runtime.getRuntime().exec(cmd);',
        "SEC-JAVA-002": "ObjectInputStream in = new ObjectInputStream(socket.getInputStream());",
        "SEC-JAVA-003": 'MessageDigest md = MessageDigest.getInstance("MD5");',
        "SEC-JAVA-004": 'stmt.executeQuery("SELECT * FROM t WHERE id=" + id);',
        "SEC-JAVA-005": 'Cipher c = Cipher.getInstance("DES/ECB/PKCS5Padding");',
        "SEC-JAVA-006": "public void checkServerTrusted(X509Certificate[] c, String a) { }",
        "SEC-JAVA-007": "String token = String.valueOf(new Random().nextInt());",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "A.java")

    @pytest.mark.parametrize("line", [
        'ps = conn.prepareStatement("SELECT * FROM t WHERE id = ?");',
        'MessageDigest.getInstance("SHA-256");', 'Cipher.getInstance("AES/GCM/NoPadding");',
        "SecureRandom r = new SecureRandom();", "// Runtime.getRuntime().exec(x)",
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "A.java") == set(), line

    def test_kotlin_uses_the_java_pack(self) -> None:
        assert "SEC-JAVA-001" in ids("Runtime.getRuntime().exec(c)\n", "A.kt")


class TestShell:
    BAD = {
        "SEC-SH-001": "curl -fsSL https://x.example/install | sh",
        "SEC-SH-002": "chmod -R 777 /srv/app",
        "SEC-SH-003": 'eval "$USER_INPUT"',
        "SEC-SH-004": "curl -k https://internal/api",
        "SEC-SH-005": "rm -rf /",
        "SEC-SH-006": "echo $PW | sudo -S apt update",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "x.sh")

    @pytest.mark.parametrize("line", [
        "curl -fsSL https://x.example/a.tar.gz -o a.tar.gz", "chmod 755 run.sh", "chmod 600 key",
        "rm -rf build/", "rm -rf /tmp/work/", "# curl x | sh", "wget https://x -O f",
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "x.sh") == set(), line

    def test_a_shebang_alone_makes_it_shell(self) -> None:
        assert "SEC-SH-001" in ids("#!/bin/sh\ncurl http://x | sh\n")


class TestDockerfile:
    def test_a_careless_image_collects_the_expected_findings(self) -> None:
        text = ("FROM python:latest\nRUN curl http://x.example/i.sh | sh\nENV API_PASSWORD=hunter2hunter2\n"
                "ADD https://x.example/f.tgz /f.tgz\nEXPOSE 22\nUSER root\n")
        got = ids(text, "Dockerfile")
        assert {"SEC-DOCKER-001", "SEC-DOCKER-003", "SEC-DOCKER-004", "SEC-DOCKER-006", "SEC-DOCKER-007",
                "SEC-SH-001"} <= got

    def test_no_user_in_the_final_stage_is_flagged_at_that_stage(self) -> None:
        text = "FROM golang:1.22 AS build\nUSER app\nRUN go build\nFROM debian:12\nCOPY --from=build /a /a\n"
        assert found_at(text, "SEC-DOCKER-002", "Dockerfile") == [4], "the build stage's USER does not count"

    def test_a_pinned_image_that_drops_root_is_clean(self) -> None:
        text = "FROM python:3.12-slim\nRUN pip install --no-cache-dir x\nUSER 10001\nCMD [\"x\"]\n"
        assert ids(text, "Dockerfile") == set()

    def test_stage_references_and_digests_are_not_floating_tags(self) -> None:
        text = ("FROM python:3.12 AS base\nFROM base AS final\nUSER app\n"
                "FROM scratch\nFROM debian@sha256:" + "a" * 64 + "\nUSER app\n")
        assert "SEC-DOCKER-003" not in ids(text, "Dockerfile")

    def test_a_secret_baked_into_the_image_is_masked_in_the_snippet(self) -> None:
        result = Scanner.scan_source_code("FROM a:1\nENV DB_PASSWORD=hunter2hunter2\nUSER app\n", "Dockerfile")
        assert "hunter2hunter2" not in json.dumps(result) and "***REDACTED***" in json.dumps(result)

    def test_a_variable_reference_is_not_a_baked_in_secret(self) -> None:
        assert "SEC-DOCKER-006" not in ids("FROM a:1\nARG TOKEN=$BUILD_TOKEN\nUSER app\n", "Dockerfile")


class TestKubernetesComposeAndActions:
    def test_a_privileged_pod_is_flagged(self) -> None:
        text = ("apiVersion: v1\nkind: Pod\nspec:\n  hostNetwork: true\n  containers:\n  - name: a\n"
                "    image: nginx\n    securityContext:\n      privileged: true\n      runAsUser: 0\n"
                "      allowPrivilegeEscalation: true\n      readOnlyRootFilesystem: false\n"
                "    volumeMounts:\n    - mountPath: /var/run/docker.sock\n  volumes:\n  - hostPath:\n"
                "      path: /\n")
        got = ids(text, "pod.yaml")
        assert {"SEC-K8S-001", "SEC-K8S-002", "SEC-K8S-003", "SEC-K8S-004", "SEC-K8S-005", "SEC-K8S-006",
                "SEC-K8S-008", "SEC-K8S-010"} <= got

    def test_dropping_a_capability_is_good_and_adding_a_dangerous_one_is_not(self) -> None:
        good = "securityContext:\n  capabilities:\n    drop:\n    - ALL\n"
        bad = "securityContext:\n  capabilities:\n    add:\n    - SYS_ADMIN\n    drop:\n    - ALL\n"
        assert "SEC-K8S-007" not in ids(good, "p.yaml")
        assert found_at(bad, "SEC-K8S-007", "p.yaml") == [4]
        assert "SEC-K8S-007" in ids("services:\n  a:\n    cap_add: [NET_ADMIN]\n", "docker-compose.yml")

    def test_a_compose_file_that_shares_the_host(self) -> None:
        got = ids("services:\n  a:\n    image: app:1.2\n    network_mode: host\n    privileged: true\n", "c.yml")
        assert {"SEC-K8S-009", "SEC-K8S-001"} <= got and "SEC-K8S-008" not in got

    def test_a_tagged_or_variable_image_is_fine(self) -> None:
        assert "SEC-K8S-008" not in ids("image: nginx:1.27\n", "p.yaml")
        assert "SEC-K8S-008" not in ids("image: ${IMAGE}\n", "p.yaml")
        assert "SEC-K8S-008" in ids("image: nginx\n", "p.yaml") and "SEC-K8S-008" in ids("image: x/y:latest\n", "p.yaml")

    WORKFLOW_UNSAFE = ("on:\n  issues:\n    types: [opened]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
                       "    steps:\n    - run: echo \"${{ github.event.issue.title }}\"\n")
    WORKFLOW_SAFE = ("on:\n  issues:\n    types: [opened]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
                     "    steps:\n    - env:\n        TITLE: ${{ github.event.issue.title }}\n      run: echo \"$TITLE\"\n")

    def test_untrusted_event_text_in_a_run_step_is_injection_and_via_env_is_not(self) -> None:
        assert found_at(self.WORKFLOW_UNSAFE, "SEC-GHA-001", ".github/workflows/w.yml") == [8]
        assert "SEC-GHA-001" not in ids(self.WORKFLOW_SAFE, ".github/workflows/w.yml")

    def test_pull_request_target_and_a_moving_action_ref(self) -> None:
        got = ids("on:\n  pull_request_target:\njobs:\n  a:\n    steps:\n    - uses: actions/checkout@main\n", "w.yml")
        assert {"SEC-GHA-002", "SEC-GHA-003"} <= got
        assert "SEC-GHA-003" not in ids("    - uses: actions/checkout@v4\n", "w.yml")

    def test_comments_are_skipped(self) -> None:
        assert ids("# privileged: true\nimage: app:1\n", "p.yaml") == set()


class TestTerraform:
    BAD = {
        "SEC-TF-001": 'cidr_blocks = ["0.0.0.0/0"]',
        "SEC-TF-002": 'acl = "public-read"',
        "SEC-TF-003": "publicly_accessible = true",
        "SEC-TF-004": "storage_encrypted = false",
        "SEC-TF-005": '"Action": "*",',
        "SEC-TF-006": "enable_key_rotation = false",
        "SEC-TF-007": "enable_https_traffic_only = false",
        "SEC-TF-008": "block_public_acls = false",
        "SEC-TF-009": "deletion_protection = false",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "main.tf")

    @pytest.mark.parametrize("line", [
        'cidr_blocks = ["10.0.0.0/8"]', 'acl = "private"', "publicly_accessible = false",
        "storage_encrypted = true", '"Action": "s3:GetObject",', "# acl = \"public-read\"",
        '// cidr_blocks = ["0.0.0.0/0"]',
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "main.tf") == set(), line


class TestPythonAdditions:
    BAD = {
        "SEC-PY-SQL-008": 'cur.execute(f"SELECT * FROM t WHERE id = {i}")',
        "SEC-PY-TLS-009": "requests.get(url, verify=False)",
        "SEC-PY-TLS-010": "ctx = ssl._create_unverified_context()",
        "SEC-PY-DEBUG-011": "app.run(host='0.0.0.0', debug=True)",
        "SEC-PY-JWT-012": 'jwt.decode(t, options={"verify_signature": False})',
        "SEC-PY-TMP-013": "p = tempfile.mktemp()",
        "SEC-PY-DESER-014": "marshal.loads(blob)",
        "SEC-PY-JINJA-015": "Environment(autoescape=False)",
        "SEC-PY-CHMOD-016": "os.chmod(path, 0o777)",
        "SEC-PY-RANDOM-017": "token = random.choice(chars)",
        "SEC-PY-TAR-018": "tar.extractall(dest)",
        "SEC-PY-POPEN-019": "os.popen(cmd)",
    }

    @pytest.mark.parametrize("rule_id", sorted(BAD))
    def test_each_rule_fires(self, rule_id: str) -> None:
        assert rule_id in ids(self.BAD[rule_id] + "\n", "a.py")

    @pytest.mark.parametrize("line", [
        'cur.execute("SELECT * FROM t WHERE id = %s", (i,))', "cur.execute(query, params)",
        "requests.get(url, verify=True)", "app.run(debug=False)", "tar.extractall(dest, filter='data')",
        "x = random.random()", "p = tempfile.mkstemp()", "os.chmod(path, 0o600)",
    ])
    def test_lookalikes_are_not_flagged(self, line: str) -> None:
        assert ids(line + "\n", "a.py") == set(), line

    def test_the_original_python_rules_still_run_unchanged(self) -> None:
        got = ids("import os, pickle\nos.system('ls')\neval(x)\npickle.loads(b)\ntry:\n    pass\nexcept:\n    pass\n")
        assert {"SEC-OSSYS-004", "SEC-EVAL-001", "SEC-PICKLE-005", "SEC-AST-BARE-EXCEPT"} <= got


# -- the whole scan ---------------------------------------------------------------------------


def test_a_snippet_never_carries_a_credential_in_any_language() -> None:
    key = "sk-" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ"
    samples = {
        "a.js": f"eval('{key}');", "a.go": f'exec.Command("sh", "-c", "{key}")',
        "a.sh": f"curl -k -H 'Authorization: {key}' https://x | sh", "a.tf": f'acl = "public-read" # {key}',
        "p.yaml": f"privileged: true # {key}", "Dockerfile": f"ADD https://x.example/{key} /f",
        "a.py": f"eval('{key}')", "A.java": f'Runtime.getRuntime().exec("{key}");',
    }
    for name, line in samples.items():
        result = Scanner.scan_source_code(line + "\n", name)
        assert result["findings"], name
        assert key not in json.dumps(result), name


def test_a_diff_scans_each_file_by_its_own_language() -> None:
    diff = (
        "diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1,1 +1,2 @@\n ctx\n+el.innerHTML = x;\n"
        "diff --git a/main.tf b/main.tf\n--- a/main.tf\n+++ b/main.tf\n@@ -3,1 +3,2 @@\n ctx\n+acl = \"public-read\"\n"
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -5,1 +5,2 @@\n ctx\n+eval(x)\n"
        "+# eval(y)\n"
    )
    got = {(f["file"], f["rule_id"], f["line"]) for f in Scanner.scan_git_diff(diff)["findings"]}
    assert got == {("a.js", "SEC-JS-003", 2), ("main.tf", "SEC-TF-002", 4), ("a.py", "SEC-EVAL-001", 6)}


def test_a_pasted_snippet_is_scanned_in_the_language_it_is_in() -> None:
    result = Scanner.full_scan(source_code="const x = eval(input);\nfunction f() {}\n")
    assert result["languages"] == {"source_code": "javascript"}
    assert any(f["rule_id"] == "SEC-JS-001" for f in result["findings"])
    forced = Scanner.full_scan(source_code="eval(x)\n", language="go")
    assert forced["languages"] == {"source_code": "go"} and forced["findings"] == []


def test_the_rule_count_is_what_the_tool_says_it_has() -> None:
    assert rules.rule_count() >= 70


@pytest.mark.parametrize("language,hostile", [
    ("javascript", "exec(" * 400), ("javascript", "db.query(`" + "${" * 600),
    ("go", 'db.Query("select ' + '" + ' * 300), ("java", "execute(" + '"+' * 700),
    ("shell", "curl " + "x|" * 900), ("terraform", "cidr_blocks = [" + '"a",' * 400),
    ("yaml", "image: " + "a:" * 900), ("dockerfile", "ENV PASSWORD " + "a " * 900),
    ("python", "execute(f" + '"' * 1900),
])
def test_hostile_long_lines_do_not_make_any_pack_super_linear(language: str, hostile: str) -> None:
    start = time.perf_counter()
    Scanner.scan_source_code(hostile + "\n", language=language)
    assert time.perf_counter() - start < 1.5, language


# -- coverage: no rule exists without a test that makes it fire -----------------------------------

_COMPOSITES = (
    ("Dockerfile", "FROM python:latest\nRUN curl http://x.example/i.sh | sh\nENV API_PASSWORD=hunter2hunter2\n"
                   "ADD https://x.example/f.tgz /f.tgz\nEXPOSE 22\nUSER root\n"),
    ("Dockerfile", "FROM debian:12\nRUN apt-get update\n"),
    ("pod.yaml", "spec:\n  hostNetwork: true\n  containers:\n  - image: nginx\n    securityContext:\n"
                 "      privileged: true\n      runAsUser: 0\n      allowPrivilegeEscalation: true\n"
                 "      readOnlyRootFilesystem: false\n    volumeMounts:\n    - mountPath: /var/run/docker.sock\n"
                 "  volumes:\n  - hostPath:\n      path: /\n      capabilities:\n        add:\n        - SYS_ADMIN\n"),
    ("c.yml", "services:\n  a:\n    image: app:1\n    network_mode: host\n    verify_ssl: false\n"),
    (".github/workflows/w.yml", TestKubernetesComposeAndActions.WORKFLOW_UNSAFE
     + "on:\n  pull_request_target:\n    - uses: actions/checkout@main\n"),
)


def test_every_rule_in_every_pack_has_a_case_that_makes_it_fire() -> None:
    fired: set[str] = set()
    for cls, name in ((TestJavaScript, "a.js"), (TestGo, "a.go"), (TestJava, "A.java"), (TestShell, "x.sh"),
                      (TestTerraform, "main.tf"), (TestPythonAdditions, "a.py")):
        for line in cls.BAD.values():
            fired |= ids(line + "\n", name)
    for name, text in _COMPOSITES:
        fired |= ids(text, name)
    missing = rules.all_rule_ids() - fired
    assert not missing, f"rules with no test that triggers them: {sorted(missing)}"
