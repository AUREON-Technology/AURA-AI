"""The shared credential catalogue: every format is found, placeholders are not, and it stays linear.

No literal secret is committed here: each sample is built at run time from a prefix and a body, so a
repository secret scanner (ours included) has nothing to flag in this file.
"""

from __future__ import annotations

import time

import pytest

from aura_core.domain import secret_catalog as cat
from aura_core.domain.safety import GuardStage
from aura_core.safety.guards import SecretGuard

_ALNUM = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ0"


def _b(n: int) -> str:
    return (_ALNUM * (n // len(_ALNUM) + 1))[:n]


def _hex(n: int) -> str:
    return ("0123456789abcdef" * (n // 16 + 1))[:n]


#: rule id -> a sample of that credential, built from parts.
SAMPLES: dict[str, str] = {
    "SEC-KEY-ANTHROPIC": "sk-" + "ant-api03-" + _b(40),
    "SEC-KEY-OPENAI": "sk-" + _b(30),
    "SEC-KEY-HUGGINGFACE": "hf_" + _b(34),
    "SEC-KEY-GROQ": "gsk_" + _b(52),
    "SEC-KEY-REPLICATE": "r8_" + _b(37),
    "SEC-KEY-GITHUB": "ghp_" + _b(36),
    "SEC-KEY-GITHUB-PAT": "github_pat_" + _b(40),
    "SEC-KEY-GITLAB": "glpat-" + _b(20),
    "SEC-KEY-NPM": "npm_" + _b(36),
    "SEC-KEY-PYPI": "pypi-AgEIcHlwaS5vcmc" + _b(60),
    "SEC-KEY-RUBYGEMS": "rubygems_" + _hex(48),
    "SEC-KEY-DOCKERHUB": "dckr_pat_" + _b(27),
    "SEC-KEY-TERRAFORM": "abcDEF123xyz45" + ".atlasv1." + _b(70),
    "SEC-KEY-DATABRICKS": "dapi" + _hex(32),
    "SEC-KEY-POSTMAN": "PMAK-" + _hex(24) + "-" + _hex(34),
    "SEC-KEY-LINEAR": "lin_api_" + _b(40),
    "SEC-KEY-NOTION": "secret_" + _b(43),
    "SEC-KEY-ATLASSIAN": "ATATT3" + _b(60),
    "SEC-KEY-SENTRY": "sntrys_eyJ" + _b(40),
    "SEC-KEY-SUPABASE": "sbp_" + _hex(40),
    "SEC-KEY-DOPPLER": "dp.pt." + _b(44),
    "SEC-KEY-DIGITALOCEAN": "dop_v1_" + _hex(64),
    "SEC-KEY-GRAFANA": "glsa_" + _b(32) + "_" + _hex(8),
    "SEC-KEY-NEWRELIC": "NRAK-" + "ABCDEFGHJKLMNPQRSTUVWXYZ012"[:27],
    "SEC-KEY-SHOPIFY": "shpat_" + _hex(32),
    "SEC-KEY-AWS": "AKIA" + "ABCDEFGHIJKLMNOP",
    "SEC-KEY-AWS-SECRET": 'aws_secret_access_key = "' + _b(40) + '"',
    "SEC-KEY-GOOGLE": "AIza" + _b(35),
    "SEC-KEY-GOOGLE-OAUTH": "ya29." + _b(30),
    "SEC-KEY-GOOGLE-SECRET": "GOCSPX-" + _b(28),
    "SEC-KEY-AZURE-STORAGE": "AccountKey=" + _b(64),
    "SEC-KEY-MAPBOX": "sk." + "eyJ" + _b(30) + "." + _b(30),
    "SEC-KEY-SLACK": "xoxb-" + "1234567890-abcdefABCDEF",
    "SEC-KEY-SLACK-APP": "xapp-1-A0123456789-1234567890123-" + _hex(24),
    "SEC-KEY-SLACK-WEBHOOK": "https://hooks.slack.com/services/T01234567/B01234567/" + _b(24),
    "SEC-KEY-STRIPE": "sk_live_" + _b(24),
    "SEC-KEY-STRIPE-TEST": "sk_test_" + _b(24),
    "SEC-KEY-STRIPE-WEBHOOK": "whsec_" + _b(32),
    "SEC-KEY-SQUARE": "sq0atp-" + _b(26),
    "SEC-KEY-TWILIO": "SK" + _hex(32),
    "SEC-KEY-SENDGRID": "SG." + _b(22) + "." + _b(43),
    "SEC-KEY-MAILGUN": "key-" + _hex(32),
    "SEC-KEY-MAILCHIMP": _hex(32) + "-us12",
    "SEC-KEY-TELEGRAM": "123456789:" + _b(35),
    "SEC-KEY-DISCORD": "M" + _b(24) + "." + "abC123" + "." + _b(27),
    "SEC-KEY-PRIVATE": "-----BEGIN RSA PRIVATE" + " KEY-----",
    "SEC-KEY-JWT": "eyJ" + _b(20) + "." + _b(20) + "." + _b(20),
    "SEC-KEY-URL-CREDENTIALS": "postgres://admin:S3cr3tPw0rd@db.internal:5432/app",
}


def test_every_format_in_the_catalogue_has_a_sample_and_there_are_about_fifty() -> None:
    assert {f.rule_id for f in cat.FORMATS} == set(SAMPLES), "add a sample for each new format"
    assert len(cat.FORMATS) >= 48


@pytest.mark.parametrize("rule_id", sorted(SAMPLES))
def test_each_format_is_recognised_by_its_own_rule(rule_id: str) -> None:
    sample = SAMPLES[rule_id]
    hits = cat.find_secrets(f"value = {sample}\n", assignments=False)
    assert [h.rule_id for h in hits] == [rule_id], hits


@pytest.mark.parametrize("rule_id", sorted(SAMPLES))
def test_the_guard_and_the_scanner_agree_on_every_format(rule_id: str) -> None:
    """One catalogue, two callers: whatever the scanner reports, the guard also redacts (and back)."""
    fmt = next(f for f in cat.FORMATS if f.rule_id == rule_id)
    text = f"hello {SAMPLES[rule_id]} world"
    verdict = SecretGuard(block=False).evaluate(text, stage=GuardStage.OUTPUT)
    if fmt.for_guard:
        assert verdict.action.name == "REDACT", rule_id
        assert SAMPLES[rule_id] not in verdict.sanitised
    else:
        assert verdict.action.name == "ALLOW", f"{rule_id} is for code review only"


def test_the_guard_keeps_the_labels_callers_already_depend_on() -> None:
    guard = SecretGuard(block=False)
    key = guard.evaluate("k " + SAMPLES["SEC-KEY-OPENAI"], stage=GuardStage.OUTPUT)
    assert "[REDACTED:api_key]" in key.sanitised
    gh = guard.evaluate("k " + SAMPLES["SEC-KEY-GITHUB"], stage=GuardStage.OUTPUT)
    assert "[REDACTED:github_token]" in gh.sanitised
    assert cat.FORMATS[0].label() == "anthropic_key"


def test_a_more_specific_format_claims_a_span_before_a_generic_one() -> None:
    hits = cat.find_secrets(SAMPLES["SEC-KEY-ANTHROPIC"], assignments=False)
    assert [h.rule_id for h in hits] == ["SEC-KEY-ANTHROPIC"], "not also reported as a generic sk- key"


class TestNamedAssignments:
    def test_a_quoted_literal_under_a_password_name_is_a_credential(self) -> None:
        for line in ('password = "hunter2hunter2"', "DB_PASSWORD: 'correct-horse-staple'",
                     '"api_key": "abcd1234efgh5678"', 'client_secret = "x9Yz8Wv7Ut6Sr5Q"',
                     'auth_token := "ZZtop-1983-ZZtop"'):
            assert [h.rule_id for h in cat.find_secrets(line)] == ["SEC-SECRET-ASSIGN"], line

    @pytest.mark.parametrize("line", [
        'password = os.environ["DB_PASSWORD"]',
        'password = "${DB_PASSWORD}"',
        'password = "changeme"',
        'api_key = "<your-api-key>"',
        'token = "{{ vault_token }}"',
        'secret = "xxxxxxxxxxxx"',
        'password = settings.db.password',
        'tokenizer = "bert-base-uncased"',
        "max_tokens = 4096",
        'password = "short"',
        'token = getenv("TOKEN_VALUE_FROM_ENV")',
    ])
    def test_placeholders_references_and_lookalikes_are_not(self, line: str) -> None:
        assert cat.find_secrets(line) == [], line

    def test_a_soft_name_needs_a_random_looking_value(self) -> None:
        random_looking = f'signing_key = "{_b(32)}"'
        assert [h.rule_id for h in cat.find_secrets(random_looking)] == ["SEC-SECRET-ENTROPY"]
        assert cat.find_secrets('signing_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"') == []
        assert cat.find_secrets('credentials = "a-very-ordinary-sentence-here"') == []

    def test_a_specific_format_under_a_secret_name_is_reported_once(self) -> None:
        hits = cat.find_secrets(f'api_key = "{SAMPLES["SEC-KEY-STRIPE"]}"')
        assert len(hits) == 1 and hits[0].rule_id == "SEC-KEY-STRIPE"


def test_text_without_a_credential_has_no_hits() -> None:
    prose = ("The deploy failed at 10:31 with exit code 2. Commit 9fceb02d0ae598e95dc970b74767f19372d61af8 "
             "fixed it; see 3f2504e0-4f89-11d3-9a0c-0305e82c3301 and https://example.com/docs/a:b.")
    assert cat.find_secrets(prose) == []


def test_mask_replaces_only_the_secret() -> None:
    text = f"before {SAMPLES['SEC-KEY-OPENAI']} after"
    assert cat.mask(text, cat.find_secrets(text)) == "before ***REDACTED:SEC-KEY-OPENAI*** after"


def test_the_allow_markers_other_scanners_use_are_honoured() -> None:
    assert cat.is_allowlisted('k = "x"  # pragma: allowlist secret')
    assert cat.is_allowlisted("k = x // gitleaks:allow")
    assert cat.is_allowlisted("k = x  # aura:allow-secret")
    assert not cat.is_allowlisted("k = x  # a secret in a comment")


def test_entropy_orders_random_above_plain() -> None:
    assert cat.shannon_entropy("") == 0.0
    assert cat.shannon_entropy("aaaaaaaa") == 0.0
    assert cat.shannon_entropy(_b(32)) > 4.0 > cat.shannon_entropy("passwordpassword")


@pytest.mark.parametrize("hostile", [
    "a" * 2000,
    "sk-" * 600,
    "://" * 600,
    "password = '" * 170,
    "api_key=" + "A" * 1990,
    '"' + "token" * 380 + '"',
    "eyJ" + "A" * 1990,
    "x:" * 1000,
    "-" * 2000,
    "AccountKey=" + "A" * 1980,
    "aws_secret " + "=" * 1980,
])
def test_a_hostile_two_thousand_character_line_is_scanned_in_linear_time(hostile: str) -> None:
    start = time.perf_counter()
    cat.find_secrets(hostile)
    cat.find_secrets(hostile, guard_only=True)
    assert time.perf_counter() - start < 1.5, "a pattern backtracks super-linearly"


def test_nothing_the_catalogue_returns_contains_a_secret_value() -> None:
    for rule_id, sample in SAMPLES.items():
        for hit in cat.find_secrets(f"x = {sample}"):
            assert not hasattr(hit, "value")
            assert sample not in repr(hit)
