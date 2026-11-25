"""One catalogue of credential formats, shared by everything that has to recognise a secret.

Two callers need the same answer to "is this text a credential?": ``SecretGuard`` (the safety
pipeline, which blocks or redacts a credential on its way to or from the model) and the
``scan_code_security`` tool (which reports hard-coded credentials in code and diffs). Before this
module each kept its own list, so a Stripe key was blocked in one place and invisible in the other.

Stdlib only (domain layer). Three kinds of rule:

* **vendor formats** -- a distinctive prefix plus a fixed alphabet and length (``AKIA...``,
  ``ghp_...``). Precise enough to run on any text, including model output.
* **structural** -- a PEM private-key header, a JWT, ``scheme://user:password@host``.
* **assignments** -- ``password = "..."`` / ``"api_key": "..."`` where only the *name* says the value
  is secret. These are the noisy ones: a placeholder (``changeme``, ``${TOKEN}``, ``<your key>``) is
  skipped, and a value whose name is merely secret-*ish* has to look random (Shannon entropy) too.

The catalogue never returns, logs or hashes a secret *value*: a hit carries the rule and the
character span, and the caller decides how to mask it (:func:`mask`).

Every pattern is anchored on a literal prefix or a name and avoids nested quantifiers; the open-ended
ones have an upper bound, so a long hostile line cannot make a scan super-linear
(``test_secret_catalog.py`` times them).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterator

__all__ = [
    "SecretFormat",
    "SecretHit",
    "FORMATS",
    "find_secrets",
    "mask",
    "shannon_entropy",
    "looks_like_placeholder",
    "is_allowlisted",
    "format_count",
]

CRITICAL, HIGH, MEDIUM = "CRITICAL", "HIGH", "MEDIUM"

#: A line carrying one of these markers is a reviewed, accepted occurrence (the markers other
#: scanners already use, so an existing allowlist keeps working).
_ALLOW = re.compile(
    r"(?:#|//|/\*|<!--)[^\n]*?(?:pragma:\s*allowlist secret|gitleaks:allow|aura:allow-secret|\bnosec\b)", re.I)


@dataclass(frozen=True)
class SecretFormat:
    """One recognisable credential shape."""

    rule_id: str
    name: str
    pattern: re.Pattern[str]
    severity: str = CRITICAL
    #: ``SecretGuard``'s label for it (``[REDACTED:<label>]``); defaults to a slug of ``name``.
    guard_label: str = ""
    #: Which regex group holds the secret (0 = the whole match).
    group: int = 0
    #: Only the structured rules are safe on arbitrary model output; assignments are not.
    for_guard: bool = True

    def label(self) -> str:
        return self.guard_label or re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")


@dataclass(frozen=True)
class SecretHit:
    """One occurrence: what matched and where -- never the value itself."""

    rule_id: str
    name: str
    severity: str
    start: int
    end: int
    label: str

    @property
    def length(self) -> int:
        return self.end - self.start


def _f(rule_id: str, name: str, regex: str, severity: str = CRITICAL, *, label: str = "",
       group: int = 0, flags: int = 0, guard: bool = True) -> SecretFormat:
    return SecretFormat(rule_id, name, re.compile(regex, flags), severity, label, group, guard)


#: Order matters only for overlaps: the first format that claims a span keeps it, so the specific
#: vendor rules sit above the generic ``sk-`` one.
FORMATS: tuple[SecretFormat, ...] = (
    # -- AI / developer platforms ---------------------------------------------------------------
    _f("SEC-KEY-ANTHROPIC", "Anthropic API key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}", label="anthropic_key"),
    _f("SEC-KEY-OPENAI", "OpenAI-style API key", r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}\b", label="api_key"),
    _f("SEC-KEY-HUGGINGFACE", "Hugging Face token", r"\bhf_[A-Za-z0-9]{30,}\b"),
    _f("SEC-KEY-GROQ", "Groq API key", r"\bgsk_[A-Za-z0-9]{40,}\b"),
    _f("SEC-KEY-REPLICATE", "Replicate API token", r"\br8_[A-Za-z0-9]{36,}\b"),
    _f("SEC-KEY-GITHUB", "GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", label="github_token"),
    _f("SEC-KEY-GITHUB-PAT", "GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", label="github_pat"),
    _f("SEC-KEY-GITLAB", "GitLab token", r"\bgl(?:pat|dt|rt|ptt|cbt|soat)-[A-Za-z0-9_\-]{20,}"),
    _f("SEC-KEY-NPM", "npm access token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    _f("SEC-KEY-PYPI", "PyPI upload token", r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}"),
    _f("SEC-KEY-RUBYGEMS", "RubyGems API key", r"\brubygems_[0-9a-f]{48}\b"),
    _f("SEC-KEY-DOCKERHUB", "Docker Hub token", r"\bdckr_pat_[A-Za-z0-9_\-]{20,}"),
    _f("SEC-KEY-TERRAFORM", "Terraform Cloud token", r"\b[A-Za-z0-9]{14}\.atlasv1\.[A-Za-z0-9_\-]{60,}"),
    _f("SEC-KEY-DATABRICKS", "Databricks token", r"\bdapi[0-9a-f]{32}\b"),
    _f("SEC-KEY-POSTMAN", "Postman API key", r"\bPMAK-[0-9a-f]{24}-[0-9a-f]{34}\b"),
    _f("SEC-KEY-LINEAR", "Linear API key", r"\blin_api_[A-Za-z0-9]{40}\b"),
    _f("SEC-KEY-NOTION", "Notion token", r"\b(?:secret_[A-Za-z0-9]{43}|ntn_[A-Za-z0-9]{40,})\b"),
    _f("SEC-KEY-ATLASSIAN", "Atlassian API token", r"\bATATT3[A-Za-z0-9_\-=]{50,}"),
    _f("SEC-KEY-SENTRY", "Sentry auth token", r"\bsntrys_eyJ[A-Za-z0-9_\-=]{30,}"),
    _f("SEC-KEY-SUPABASE", "Supabase access token", r"\bsbp_[0-9a-f]{40}\b"),
    _f("SEC-KEY-DOPPLER", "Doppler token", r"\bdp\.(?:pt|st|sa|ct|scim|audit)\.[A-Za-z0-9]{40,}\b"),
    _f("SEC-KEY-DIGITALOCEAN", "DigitalOcean token", r"\bdo[opr]_v1_[a-f0-9]{64}\b"),
    _f("SEC-KEY-GRAFANA", "Grafana service-account token", r"\bglsa_[A-Za-z0-9]{32}_[0-9a-f]{8}\b"),
    _f("SEC-KEY-NEWRELIC", "New Relic user key", r"\bNRAK-[A-Z0-9]{27}\b"),
    _f("SEC-KEY-SHOPIFY", "Shopify token", r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b"),
    # -- cloud --------------------------------------------------------------------------------
    _f("SEC-KEY-AWS", "AWS access key ID", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", label="aws_access_key"),
    _f("SEC-KEY-AWS-SECRET", "AWS secret access key",
       r"(?i)aws[_\-\s]?(?:secret|private)[A-Za-z0-9_\-\s]{0,24}['\"\s:=]{1,6}([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])",
       group=1, guard=False),
    _f("SEC-KEY-GOOGLE", "Google API key", r"\bAIza[0-9A-Za-z_\-]{30,}\b", label="google_api_key"),
    _f("SEC-KEY-GOOGLE-OAUTH", "Google OAuth access token", r"\bya29\.[A-Za-z0-9_\-]{20,}", label="google_oauth_token"),
    _f("SEC-KEY-GOOGLE-SECRET", "Google OAuth client secret", r"\bGOCSPX-[A-Za-z0-9_\-]{10,}"),
    _f("SEC-KEY-AZURE-STORAGE", "Azure storage account key",
       r"\bAccountKey=[A-Za-z0-9+/]{60,}={0,2}", label="azure_storage_key"),
    _f("SEC-KEY-MAPBOX", "Mapbox secret token", r"\bsk\.eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),
    # -- messaging / payments / email ---------------------------------------------------------
    _f("SEC-KEY-SLACK", "Slack token", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b", label="slack_token"),
    _f("SEC-KEY-SLACK-APP", "Slack app token", r"\bxapp-\d-[A-Z0-9]{8,}-\d{8,}-[a-f0-9]{20,}\b"),
    _f("SEC-KEY-SLACK-WEBHOOK", "Slack webhook URL",
       r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{20,}", HIGH),
    _f("SEC-KEY-STRIPE", "Stripe secret key", r"\b[sr]k_live_[A-Za-z0-9]{16,}\b"),
    _f("SEC-KEY-STRIPE-TEST", "Stripe test key", r"\b[sr]k_test_[A-Za-z0-9]{16,}\b", MEDIUM),
    _f("SEC-KEY-STRIPE-WEBHOOK", "Stripe webhook secret", r"\bwhsec_[A-Za-z0-9]{24,}\b", HIGH),
    _f("SEC-KEY-SQUARE", "Square access token", r"\bsq0(?:atp|csp)-[A-Za-z0-9_\-]{22,}"),
    _f("SEC-KEY-TWILIO", "Twilio API key", r"\bSK[0-9a-fA-F]{32}\b"),
    _f("SEC-KEY-SENDGRID", "SendGrid API key", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    _f("SEC-KEY-MAILGUN", "Mailgun API key", r"\bkey-[0-9a-f]{32}\b"),
    _f("SEC-KEY-MAILCHIMP", "Mailchimp API key", r"\b[0-9a-f]{32}-us\d{1,2}\b"),
    _f("SEC-KEY-TELEGRAM", "Telegram bot token", r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b", guard=False),
    _f("SEC-KEY-DISCORD", "Discord bot token", r"\b[MNO][A-Za-z\d_\-]{23,25}\.[A-Za-z\d_\-]{6}\.[A-Za-z\d_\-]{27,38}\b", guard=False),
    # -- structural ---------------------------------------------------------------------------
    _f("SEC-KEY-PRIVATE", "Private key block",
       r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
       r"(?:[\s\S]{0,8192}?(?:-----END (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----|\Z))?",
       label="private_key"),
    _f("SEC-KEY-JWT", "JSON Web Token", r"\beyJ[A-Za-z0-9_\-]{15,2048}\.[A-Za-z0-9_\-]{15,2048}\.[A-Za-z0-9_\-]{10,2048}\b",
       HIGH, label="jwt"),
    _f("SEC-KEY-URL-CREDENTIALS", "Credentials in a URL",
       r"\b[a-z][a-z0-9+.\-]{1,15}://[^\s:/@'\"<>${}]{1,64}:([^\s:/@'\"<>${}]{3,64})@[A-Za-z0-9.\-\[\]]{1,100}",
       HIGH, label="url_credentials", group=1, guard=False),
)

# -- assignments (name says it is secret) ------------------------------------------------------

_PREFIX = r"[A-Za-z0-9_.\-]{0,40}"
#: A name ends in the keyword (optionally ``_key``/``_value``...): ``db_password`` yes, ``tokenizer`` no.
_SUFFIX = r"(?:[_-]?(?:key|value|string|str|secret|id))?"
#: Names that are credentials on their own: a quoted literal of any length is one.
_STRONG_NAMES = (r"(?:password|passwd|pwd|passphrase|secret(?:[_-]?key)?|client[_-]?secret|private[_-]?key|"
                 r"api[_-]?key|api[_-]?secret|apikey|(?:access[_-]?|auth[_-]?|refresh[_-]?)?token)")
#: Names that usually hold one, so the value must also look random.
_SOFT_NAMES = r"(?:access[_-]?key|auth[_-]?key|signing[_-]?key|encryption[_-]?key|credentials?|bearer)"

_ASSIGN_STRONG = re.compile(
    rf"(?i)(?<![A-Za-z0-9])({_PREFIX}{_STRONG_NAMES}{_SUFFIX})['\"]?\s*(?::=|=>|[:=])\s*(['\"`])([^'\"`\n]{{8,200}})\2"
)
#: ``DB_PASSWORD=Sup3rS3cret`` / ``password: hunter2prod``: a whole line, no quotes (.env, ini, YAML). The value
#: needs a letter and a digit and no ``(`` ``.`` ``[``, so ``password = request.form`` is not a literal.
_ASSIGN_BARE = re.compile(
    rf"(?im)^[ \t]*(?:export[ \t]+)?({_PREFIX}{_STRONG_NAMES}{_SUFFIX})[ \t]*[:=][ \t]*"
    r"([A-Za-z0-9_\-+/=@!#$%^&*~]{8,200})[ \t]*(?:#.*)?$"
)
_ASSIGN_SOFT = re.compile(
    rf"(?i)(?<![A-Za-z0-9])({_PREFIX}{_SOFT_NAMES}{_SUFFIX})['\"]?\s*(?::=|=>|[:=])\s*(['\"`]?)([A-Za-z0-9_\-+/=.~]{{20,200}})\2"
)

_PLACEHOLDER_SUBSTRINGS = (
    "changeme", "change_me", "change-me", "your_", "your-", "yourkey", "dummy", "placeholder", "fixme",
    "redacted", "secretkey", "mysecret", "password123", "notasecret", "not-a-secret", "foobar", "lorem",
    "xxxx", "****", "....", "${", "{{", "%(", "os.environ", "getenv", "process.env", "env(", "config(",
    "settings.", "secrets.", "vault:",
)
#: Words that mark a stand-in only when they are a whole word: ``Example2024!x`` is a password.
_PLACEHOLDER_WORDS = re.compile(r"(?<![a-z0-9])(?:example|sample|replace|todo|test|none|null|secret|password)(?![a-z0-9])")
_PLACEHOLDER_ANGLE = re.compile(r"^\s*<[^<>]{1,80}>\s*$")


def shannon_entropy(value: str) -> float:
    """Bits per character of ``value``'s own character distribution (0 for empty)."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(value)
    return -sum(c / total * math.log2(c / total) for c in counts.values())


def looks_like_placeholder(value: str) -> bool:
    """True for a value that is plainly not a real secret (a template, an env lookup, a stand-in)."""
    low = value.strip().lower()
    if not low or any(word in low for word in _PLACEHOLDER_SUBSTRINGS):
        return True
    if _PLACEHOLDER_ANGLE.match(low) or low in ("%s", "%d") or _PLACEHOLDER_WORDS.fullmatch(low):
        return True
    if len(set(low)) <= 2:  # "aaaaaaaa", "00000000", "********"
        return True
    return bool(re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", low))  # ``settings.db.password``: an attribute path


def is_allowlisted(line: str) -> bool:
    """True when the line carries an accepted-occurrence marker *in a comment* (a marker inside a
    string literal, or a word that merely contains ``nosec``, does not count)."""
    return bool(_ALLOW.search(line))


def _iter_assignments(text: str) -> Iterator[tuple[str, str, int, int, str]]:
    """``(rule_id, name, start, end, severity)`` for each assignment whose value looks secret."""
    for match in _ASSIGN_STRONG.finditer(text):
        value = match.group(3)
        if not looks_like_placeholder(value):
            yield "SEC-SECRET-ASSIGN", "Hard-coded credential", match.start(3), match.end(3), HIGH
    for match in _ASSIGN_BARE.finditer(text):
        value = match.group(2)
        if (re.search(r"[A-Za-z]", value) and re.search(r"\d", value) and not looks_like_placeholder(value)
                and not value.startswith("$")):
            yield "SEC-SECRET-ASSIGN", "Hard-coded credential", match.start(2), match.end(2), HIGH
    for match in _ASSIGN_SOFT.finditer(text):
        value = match.group(3)
        if looks_like_placeholder(value) or shannon_entropy(value) < 3.5 or not re.search(r"\d", value):
            continue
        yield "SEC-SECRET-ENTROPY", "High-entropy value under a secret-looking name", match.start(3), match.end(3), HIGH


def find_secrets(text: str, *, assignments: bool = True, guard_only: bool = False) -> list[SecretHit]:
    """Every credential in ``text``, non-overlapping, in order of position.

    ``guard_only`` keeps just the formats safe to run on arbitrary text (what ``SecretGuard`` uses);
    ``assignments=False`` drops the name-based rules.
    """
    hits: list[SecretHit] = []
    claimed: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in claimed)

    for fmt in FORMATS:
        if guard_only and not fmt.for_guard:
            continue
        for match in fmt.pattern.finditer(text):
            start, end = match.start(fmt.group), match.end(fmt.group)
            if start == end or not free(start, end):
                continue
            if fmt.rule_id == "SEC-KEY-URL-CREDENTIALS" and looks_like_placeholder(match.group(fmt.group)):
                continue
            claimed.append((start, end))
            hits.append(SecretHit(fmt.rule_id, fmt.name, fmt.severity, start, end, fmt.label()))
    if assignments and not guard_only:
        for rule_id, name, start, end, severity in _iter_assignments(text):
            if free(start, end):
                claimed.append((start, end))
                hits.append(SecretHit(rule_id, name, severity, start, end, "credential"))
    return sorted(hits, key=lambda h: h.start)


def mask(text: str, hits: list[SecretHit]) -> str:
    """``text`` with each hit replaced by ``***REDACTED:<rule>***`` (hits must not overlap)."""
    out: list[str] = []
    cursor = 0
    for hit in sorted(hits, key=lambda h: h.start):
        if hit.start < cursor:
            continue
        out.append(text[cursor:hit.start])
        out.append(f"***REDACTED:{hit.rule_id}***")
        cursor = hit.end
    out.append(text[cursor:])
    return "".join(out)


def format_count() -> int:
    """How many distinct credential formats the catalogue recognises (assignment rules included)."""
    return len(FORMATS) + 2
