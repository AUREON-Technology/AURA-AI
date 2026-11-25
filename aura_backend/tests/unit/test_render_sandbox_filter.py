"""``AURA_SANDBOX_ALLOWED_EGRESS_HOSTS`` is real: the renderer, the gateway check, and the compose wiring.

The setting used to be parsed and read by nothing; the proxy's allowlist was the static file
``deploy/sandbox/filter``. ``deploy/scripts/render-sandbox-filter.sh`` now builds the proxy's
filter from that file plus the validated hosts. These tests run the *real script* in a subprocess
against fixtures -- what it writes is what tinyproxy would read.

What is NOT covered here, because it cannot run in this environment: the compose services
themselves (``sandbox-proxy-filter`` -> ``sandbox-proxy``) and tinyproxy refusing a host that is
not in the rendered file. That needs Docker; ``deploy/scripts/README.md`` says how to check it on a
host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from aura_core.config.settings import egress_host_problem, load_settings

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "scripts" / "render-sandbox-filter.sh"
BASE = REPO / "deploy" / "sandbox" / "filter"
BEGIN = "# --- BEGIN hosts from AURA_SANDBOX_ALLOWED_EGRESS_HOSTS"
END = "# --- END generated hosts ---"

SHELLS = [s for s in ("sh", "dash", "bash", "busybox") if shutil.which(s)]


def run(hosts: str | None = None, *, base: Path = BASE, out: Path | None = None, shell: str = "sh",
        env_hosts: str | None = None, cwd: Path | None = None, extra: list[str] | None = None):
    argv = [shell] if shell != "busybox" else ["busybox", "sh"]
    argv += [str(SCRIPT), "--base", str(base)]
    if hosts is not None:
        argv += ["--hosts", hosts]
    if out is not None:
        argv += ["--out", str(out)]
    argv += extra or []
    env = {k: v for k, v in os.environ.items() if k != "AURA_SANDBOX_ALLOWED_EGRESS_HOSTS"}
    if env_hosts is not None:
        env["AURA_SANDBOX_ALLOWED_EGRESS_HOSTS"] = env_hosts
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=30)


def lines_of(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


class TestEmptyInputIsTheStaticFile:
    @pytest.mark.parametrize("shell", SHELLS)
    def test_no_hosts_gives_the_base_byte_for_byte(self, shell: str) -> None:
        for result in (run(shell=shell), run("", shell=shell), run(" , ,\n", shell=shell),
                       run(shell=shell, env_hosts="")):
            assert result.returncode == 0, result.stderr
            assert result.stdout == BASE.read_text()

    def test_the_static_file_is_still_the_default_allowlist(self) -> None:
        assert lines_of(BASE.read_text()) == [
            r"^github\.com$", r"^api\.github\.com$", r"^codeload\.github\.com$",
            r"^objects\.githubusercontent\.com$", r"^pypi\.org$", r"^files\.pythonhosted\.org$",
            r"^registry\.npmjs\.org$",
        ]


class TestRendering:
    @pytest.mark.parametrize("shell", SHELLS)
    def test_hosts_are_appended_lowercased_escaped_and_anchored(self, shell: str) -> None:
        result = run("Example.COM, api.my-svc.example.org\nfiles.example.net", shell=shell)
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert out.startswith(BASE.read_text()), "the base is preserved untouched"
        generated = out[len(BASE.read_text()):]
        assert generated.splitlines() == [
            next(l for l in generated.splitlines() if l.startswith(BEGIN)),
            r"^example\.com$",
            r"^api\.my-svc\.example\.org$",
            r"^files\.example\.net$",
            END,
        ]

    def test_the_hosts_can_come_from_the_environment_variable(self) -> None:
        result = run(env_hosts="a.example.com,b.example.com")
        assert result.returncode == 0
        assert r"^a\.example\.com$" in result.stdout and r"^b\.example\.com$" in result.stdout

    def test_an_argument_beats_the_environment(self) -> None:
        result = run("arg.example.com", env_hosts="env.example.com")
        assert r"^arg\.example\.com$" in result.stdout and "env" not in result.stdout.split(BEGIN)[1]

    def test_duplicates_are_kept_once(self) -> None:
        result = run("a.example.com, A.example.com, a.example.com")
        assert result.stdout.count(r"^a\.example\.com$") == 1

    def test_each_generated_line_matches_exactly_its_host(self) -> None:
        """What the proxy does with a line: a case-insensitive regex search over the host name."""
        result = run("api.partner.example.com")
        pattern = next(l for l in lines_of(result.stdout) if "partner" in l)
        regex = re.compile(pattern, re.IGNORECASE)
        assert regex.search("api.partner.example.com") and regex.search("API.Partner.Example.com")
        for outside in ("apiXpartner.example.com", "evil-api.partner.example.com",
                        "api.partner.example.com.evil.net", "api.partner.example.comx", "partner.example.com"):
            assert not regex.search(outside), outside

    def test_a_dot_in_a_host_is_not_a_regex_wildcard(self) -> None:
        pattern = next(l for l in lines_of(run("a.example.com").stdout) if l.startswith(r"^a"))
        assert not re.compile(pattern).search("aXexample.com")


class TestRefusals:
    @pytest.mark.parametrize("bad,why", [
        ("*.example.com", "wildcard"),
        ("*", "wildcard"),
        ("https://example.com", "bare host"),
        ("example.com/path", "bare host"),
        ("example.com:8443", "port"),
        ("user@example.com", "credentials"),
        ("localhost", "two labels"),
        ("10.0.0.5", "IP address"),
        ("127.0.0.1", "IP address"),
        ("exa mple.com", None),  # splits into two entries, the first of which has one label
        ("exam_ple.com", "letters, digits"),
        ("bücher.example", "letters, digits"),
        (".example.com", "malformed"),
        ("example.com.", "malformed"),
        ("-bad.example.com", "malformed"),
        ("bad-.example.com", "malformed"),
        ("a..example.com", "malformed"),
        ("a" * 64 + ".example.com", "63"),
        ("a.b.c." + "d" * 250 + ".com", "253"),
        ("$(touch pwned).example.com", None),
        ("example.com;rm -rf", None),
        ("example.com`id`", "letters, digits"),
    ])
    def test_an_invalid_host_is_refused_with_its_name_in_the_message(self, bad: str, why: str | None, tmp_path) -> None:
        result = run(bad, cwd=tmp_path)
        assert result.returncode == 2, (bad, result.stdout, result.stderr)
        assert "invalid host" in result.stderr
        if why:
            assert why in result.stderr
        assert result.stdout == "", "nothing is printed for a refused input"
        assert not list(tmp_path.iterdir()), "and nothing ran: no file was created by an embedded command"

    def test_one_bad_host_refuses_the_whole_list(self, tmp_path: Path) -> None:
        target = tmp_path / "filter.out"
        target.write_text("previous good filter\n")
        result = run("good.example.com, *.bad.example.com", out=target)
        assert result.returncode == 2
        assert target.read_text() == "previous good filter\n", "a refused render must not touch the output"
        assert not list(tmp_path.glob("filter.out.tmp*"))

    def test_a_glob_character_is_never_expanded_against_the_directory(self, tmp_path: Path) -> None:
        (tmp_path / "secret-file.example.com").write_text("x")
        result = run("*", cwd=tmp_path)
        assert result.returncode == 2 and "'*'" in result.stderr

    def test_too_many_hosts_is_refused(self) -> None:
        hosts = ",".join(f"h{i}.example.com" for i in range(101))
        result = run(hosts)
        assert result.returncode == 2 and "100" in result.stderr

    def test_a_missing_base_is_an_error_not_an_empty_allowlist(self, tmp_path: Path) -> None:
        result = run("a.example.com", base=tmp_path / "nope")
        assert result.returncode == 1 and "cannot read the base filter" in result.stderr

    def test_check_mode_validates_without_rendering(self) -> None:
        ok = run("a.example.com", extra=["--check"])
        bad = run("*.example.com", extra=["--check"])
        assert ok.returncode == 0 and ok.stdout == ""
        assert bad.returncode == 2

    def test_an_unknown_argument_is_refused(self) -> None:
        assert run(extra=["--frobnicate"]).returncode == 2


class TestIdempotent:
    def test_the_same_input_twice_is_the_same_bytes(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        assert run("a.example.com,b.example.com", out=first).returncode == 0
        assert run("a.example.com,b.example.com", out=second).returncode == 0
        assert first.read_bytes() == second.read_bytes()

    def test_rendering_over_its_own_output_does_not_stack_sections(self, tmp_path: Path) -> None:
        once = tmp_path / "once"
        run("a.example.com,b.example.com", out=once)
        twice = tmp_path / "twice"
        run("a.example.com,b.example.com", base=once, out=twice)
        assert twice.read_bytes() == once.read_bytes()
        assert once.read_text().count(BEGIN) == 1 and twice.read_text().count(BEGIN) == 1

    def test_changing_the_hosts_replaces_the_generated_section(self, tmp_path: Path) -> None:
        once, again = tmp_path / "once", tmp_path / "again"
        run("old.example.com", out=once)
        run("new.example.com", base=once, out=again)
        text = again.read_text()
        assert r"^new\.example\.com$" in text and "old" not in text

    def test_dropping_the_hosts_returns_to_the_static_file(self, tmp_path: Path) -> None:
        once, empty = tmp_path / "once", tmp_path / "empty"
        run("old.example.com", out=once)
        run("", base=once, out=empty)
        assert empty.read_text() == BASE.read_text()

    def test_the_output_file_is_world_readable_for_the_proxy(self, tmp_path: Path) -> None:
        target = tmp_path / "filter"
        run("a.example.com", out=target)
        assert target.stat().st_mode & 0o044 == 0o044


class TestGatewayAndScriptAgree:
    """One corpus, two implementations: the gateway's start-up check must refuse exactly what the
    renderer refuses, or a deployment boots fine and its proxy container then exits."""

    CORPUS = [
        "example.com", "a.b.example.com", "api-1.example.co.uk", "XN--BCHER-KVA.example",
        "*.example.com", "*", "https://example.com", "example.com/x", "example.com:443",
        "user@example.com", "localhost", "10.1.2.3", "exam_ple.com", "bücher.example",
        ".example.com", "example.com.", "-a.example.com", "a-.example.com", "a..b.com",
        "a" * 63 + ".example.com", "a" * 64 + ".example.com", "1.example.com", "example.123",
    ]

    @pytest.mark.parametrize("host", CORPUS)
    def test_agreement(self, host: str) -> None:
        script_refuses = run(host, extra=["--check"]).returncode == 2
        assert (egress_host_problem(host) is not None) == script_refuses, host

    def test_the_gateway_refuses_an_invalid_value_at_start_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from aura_core.domain.errors import AuraError

        monkeypatch.setenv("AURA_SANDBOX_ALLOWED_EGRESS_HOSTS", "good.example.com, *.evil.example.com")
        settings = load_settings(refresh=True, validate=False)
        assert settings.sandbox.allowed_egress_hosts == ("good.example.com", "*.evil.example.com")
        with pytest.raises(AuraError) as caught:
            settings.validate()
        assert "AURA_SANDBOX_ALLOWED_EGRESS_HOSTS entry '*.evil.example.com'" in str(caught.value.details)

    def test_whitespace_and_commas_both_separate_hosts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_SANDBOX_ALLOWED_EGRESS_HOSTS", "a.example.com b.example.com,\nc.example.com")
        settings = load_settings(refresh=True, validate=False)
        assert settings.sandbox.allowed_egress_hosts == ("a.example.com", "b.example.com", "c.example.com")

    def test_valid_hosts_and_the_unset_default_pass_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AURA_SANDBOX_ALLOWED_EGRESS_HOSTS", raising=False)
        assert load_settings(refresh=True, validate=False).sandbox.allowed_egress_hosts == ()


class TestComposeWiring:
    """Structure only. That the containers behave is Docker's to show, not this suite's."""

    FILES = {"prod": REPO / "deploy" / "docker-compose.prod.yml", "dev": REPO / "deploy" / "docker-compose.dev.yml"}
    GATEWAY = {"prod": "gateway", "dev": "dev-gateway"}

    @pytest.fixture(scope="class", params=["prod", "dev"])
    def stack(self, request):
        return request.param

    @pytest.fixture(scope="class")
    def compose(self, stack):
        return yaml.safe_load(self.FILES[stack].read_text())

    @pytest.fixture(scope="class")
    def gateway(self, stack, compose):
        return compose["services"][self.GATEWAY[stack]]

    def test_the_proxy_reads_the_rendered_filter_after_the_renderer_succeeds(self, compose) -> None:
        proxy = compose["services"]["sandbox-proxy"]
        assert proxy["depends_on"]["sandbox-proxy-filter"] == {"condition": "service_completed_successfully"}
        mounts = " ".join(proxy["volumes"])
        assert "/etc/tinyproxy/rendered:ro" in mounts
        assert "./sandbox/filter:/etc/tinyproxy/filter" not in mounts, "the static file is no longer read directly"

    def test_the_renderer_service_gets_the_setting_the_script_and_the_static_base(self, compose) -> None:
        renderer = compose["services"]["sandbox-proxy-filter"]
        assert renderer["profiles"] == ["sandbox"]
        assert "AURA_SANDBOX_ALLOWED_EGRESS_HOSTS" in renderer["environment"]
        mounts = " ".join(renderer["volumes"])
        assert "./scripts/render-sandbox-filter.sh:/render/render-sandbox-filter.sh:ro" in mounts
        assert "./sandbox/filter:/render/filter:ro" in mounts, "the static file stays the base"
        assert renderer["network_mode"] == "none" and renderer["read_only"] is True
        assert renderer["cap_drop"] == ["ALL"] and renderer["restart"] == "no"

    def test_the_gateway_receives_the_setting_so_it_can_validate_it(self, gateway) -> None:
        assert "AURA_SANDBOX_ALLOWED_EGRESS_HOSTS" in gateway["environment"]

    def test_the_default_is_empty_so_nothing_changes_unless_set(self, compose, gateway) -> None:
        assert gateway["environment"]["AURA_SANDBOX_ALLOWED_EGRESS_HOSTS"] == \
            "${AURA_SANDBOX_ALLOWED_EGRESS_HOSTS:-}"
        assert compose["services"]["sandbox-proxy-filter"]["environment"]["AURA_SANDBOX_ALLOWED_EGRESS_HOSTS"] == \
            "${AURA_SANDBOX_ALLOWED_EGRESS_HOSTS:-}"

    def test_prod_and_dev_render_identically(self) -> None:
        prod, dev = (yaml.safe_load(p.read_text())["services"]["sandbox-proxy-filter"] for p in self.FILES.values())
        for key in ("entrypoint", "network_mode", "read_only", "cap_drop", "security_opt", "user", "profiles"):
            assert prod[key] == dev[key], key

    def test_tinyproxy_reads_where_the_proxy_mounts_the_rendered_file(self) -> None:
        conf = (REPO / "deploy" / "sandbox" / "tinyproxy.conf").read_text()
        assert 'Filter "/etc/tinyproxy/rendered/filter"' in conf
        assert "FilterDefaultDeny Yes" in conf, "an unlisted host stays refused"
