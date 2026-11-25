"""Per-language rule packs for ``scan_code_security`` (pure Python, no external scanner).

The scanner used to know seven Python constructs. Real repositories are mostly not Python, so each
pack here covers the usual high-signal mistakes of one language or file kind: JavaScript/TypeScript,
Go, Java/Kotlin, shell, Dockerfile, Kubernetes/Compose/GitHub-Actions YAML and Terraform, plus a few
more for Python.

Every rule is **one line** of one language and reports a rule id, a severity and a message.
Multi-line facts (a Dockerfile with no ``USER``, a capability added in a YAML list) are small
*file rules*. None of this is a data-flow analysis: a hit means "this construct is present", and the
report says so. Deeper analysis is Semgrep's job, in the sandbox (``security_external.py``).

Patterns use bounded, non-nested quantifiers so a hostile line cannot make a scan super-linear.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

__all__ = ["Rule", "FileRule", "RULE_PACKS", "LANGUAGES", "detect_language", "line_rules", "file_rules",
           "is_comment_line", "rule_count", "all_rule_ids"]

LANGUAGES = ("python", "javascript", "go", "java", "shell", "dockerfile", "yaml", "terraform")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    severity: str
    pattern: re.Pattern[str]
    message: str
    #: A second pattern the same line must also match (the rule is about a *kind of value*).
    requires: Optional[re.Pattern[str]] = None
    #: The snippet would carry a secret value: mask what follows the name.
    masks_value: bool = False
    #: The line is a credential in all but name (``echo pw | sudo -S``): the snippet is replaced outright.
    withhold: bool = False


@dataclass(frozen=True)
class FileRule:
    rule_id: str
    severity: str
    message: str
    #: ``lines -> [(line_number, snippet)]``
    check: Callable[[Sequence[str]], list[tuple[int, str]]]


def _r(rule_id: str, severity: str, regex: str, message: str, *, flags: int = 0, requires: str = "",
       masks: bool = False, withhold: bool = False) -> Rule:
    return Rule(rule_id, severity, re.compile(regex, flags), message,
                re.compile(requires, re.I) if requires else None, masks, withhold)


_TOKEN_CONTEXT = r"token|secret|passw|api[_-]?key|session|otp|nonce|csrf|salt|\bkey\b"

# -- Python (in addition to the seven rules the scanner has always had) -------------------------

_PYTHON = (
    _r("SEC-PY-SQL-008", "HIGH",
       r"""\.execute(?:many|script)?\s*\(\s*(?:[fF]["']|["'][^"']*["']\s*(?:%\s*[(\w]|\+\s*[\w(]|\.format\s*\())""",
       "SQL built from a string (f-string, %, + or .format) passed to execute(): use bound parameters."),
    _r("SEC-PY-TLS-009", "HIGH", r"\bverify\s*=\s*False\b",
       "TLS certificate verification is turned off (verify=False)."),
    _r("SEC-PY-TLS-010", "HIGH", r"\bssl\._create_unverified_context\b|\bssl\.CERT_NONE\b|\bcheck_hostname\s*=\s*False\b",
       "TLS certificate or hostname verification is disabled."),
    _r("SEC-PY-DEBUG-011", "MEDIUM", r"\.run\s*\([^)\n]*\bdebug\s*=\s*True|^\s*DEBUG\s*=\s*True\b",
       "Debug mode is enabled: Flask/Django debug pages expose code and secrets."),
    _r("SEC-PY-JWT-012", "HIGH",
       r"""["']verify_signature["']\s*:\s*False|algorithms\s*=\s*\[\s*["']none["']""",
       "JWT signature verification is disabled or the 'none' algorithm is accepted."),
    _r("SEC-PY-TMP-013", "MEDIUM", r"\btempfile\.mktemp\s*\(",
       "tempfile.mktemp() is racy: use NamedTemporaryFile or mkstemp."),
    _r("SEC-PY-DESER-014", "MEDIUM", r"\b(?:marshal|dill|jsonpickle)\.(?:loads?|decode)\s*\(",
       "Deserialization of untrusted data can run code."),
    _r("SEC-PY-JINJA-015", "MEDIUM", r"\bautoescape\s*=\s*False\b",
       "Template auto-escaping is disabled: cross-site scripting."),
    _r("SEC-PY-CHMOD-016", "MEDIUM", r"\bos\.chmod\s*\([^)\n]*\b0o?777\b",
       "World-writable permissions (0777)."),
    _r("SEC-PY-RANDOM-017", "LOW", r"\brandom\.(?:random|randint|randrange|choice|choices|getrandbits)\s*\(",
       "random is not cryptographically secure: use the secrets module for tokens and keys.",
       requires=_TOKEN_CONTEXT),
    _r("SEC-PY-TAR-018", "MEDIUM", r"\.extractall\s*\((?![^)\n]*\bfilter\s*=)",
       "extractall() without a filter can write outside the target folder (path traversal)."),
    _r("SEC-PY-POPEN-019", "HIGH", r"(?<![.\w])\bos\.popen\s*\(", "OS command execution via os.popen()."),
)

# -- JavaScript / TypeScript -----------------------------------------------------------------

_JS = (
    _r("SEC-JS-001", "HIGH", r"(?<![.\w$])eval\s*\(", "Dynamic code execution via eval()."),
    _r("SEC-JS-002", "HIGH", r"\bnew\s+Function\s*\(", "Dynamic code execution via new Function()."),
    _r("SEC-JS-003", "MEDIUM",
       r"""\.(?:inner|outer)HTML\s*\+?=(?!=)(?!\s*(?:'[^'$]*'|"[^"$]*")\s*;?\s*$)""",
       "Assigning a non-literal to innerHTML/outerHTML: cross-site scripting."),
    _r("SEC-JS-004", "MEDIUM", r"\bdocument\.write(?:ln)?\s*\(", "document.write() injects markup: cross-site scripting."),
    _r("SEC-JS-005", "MEDIUM", r"\bdangerouslySetInnerHTML\b", "dangerouslySetInnerHTML bypasses React's escaping."),
    _r("SEC-JS-006", "HIGH",
       r"""(?:\bchild_process\.exec(?:Sync)?|(?<![.\w$])exec(?:Sync)?)\s*\(\s*(?!(?:'[^'$]*'|"[^"$]*")\s*[,)])""",
       "Shell command built from a non-literal passed to exec()/execSync(): command injection."),
    _r("SEC-JS-007", "MEDIUM", r"""\b(?:setTimeout|setInterval)\s*\(\s*['"`]""",
       "setTimeout/setInterval with a string argument evaluates code."),
    _r("SEC-JS-008", "HIGH", r"rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0",
       "TLS certificate verification is turned off."),
    _r("SEC-JS-009", "LOW", r"""createHash\(\s*['"](?:md5|sha1)['"]""", "Weak hash algorithm (MD5/SHA1)."),
    _r("SEC-JS-010", "LOW", r"\bMath\.random\s*\(", "Math.random() is not cryptographically secure.",
       requires=_TOKEN_CONTEXT),
    _r("SEC-JS-011", "HIGH",
       r"""\.(?:query|execute|raw)\s*\(\s*(?:`[^`]*\$\{|['"][^'"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^'"]*['"]\s*\+)""",
       "SQL built by string concatenation or interpolation: use bound parameters.", flags=re.I),
    _r("SEC-JS-012", "MEDIUM",
       r"""access-control-allow-origin['"]?\s*[:,]\s*['"]?\*|\borigin\s*:\s*['"]\*['"]""",
       "CORS allows every origin.", flags=re.I),
    _r("SEC-JS-013", "MEDIUM", r"\bvm\.run(?:InNewContext|InThisContext|InContext)\s*\(",
       "The vm module is not a sandbox."),
    _r("SEC-JS-014", "HIGH", r"""algorithms?\s*:\s*\[\s*['"]none['"]""", "JWT 'none' algorithm accepted."),
    _r("SEC-JS-015", "LOW", r"""localStorage\.setItem\(\s*['"][^'"]*(?:token|jwt|secret|password)""",
       "A credential stored in localStorage is readable by any script on the page.", flags=re.I),
)

# -- Go ----------------------------------------------------------------------------------------

_GO = (
    _r("SEC-GO-001", "HIGH", r"InsecureSkipVerify\s*:\s*true", "TLS certificate verification is turned off."),
    _r("SEC-GO-002", "HIGH",
       r"""exec\.Command(?:Context)?\s*\([^)\n]*["'](?:sh|bash|/bin/sh|/bin/bash)["']\s*,\s*["']-c["']""",
       "Command run through a shell (sh -c): command injection if any part is user data."),
    _r("SEC-GO-003", "LOW", r"\b(?:md5|sha1)\.(?:New|Sum)\s*\(", "Weak hash algorithm (MD5/SHA1)."),
    _r("SEC-GO-004", "HIGH", r"\.(?:Query|Exec|QueryRow)(?:Context)?\s*\([^)\n]*(?:fmt\.Sprintf\s*\(|\"\s*\+)",
       "SQL built by Sprintf or concatenation: use placeholders.", requires=r"\b(?:select|insert|update|delete)\b"),
    _r("SEC-GO-005", "MEDIUM",
       r"(?:ioutil\.WriteFile|os\.(?:WriteFile|Chmod|MkdirAll|Mkdir|OpenFile))\s*\([^)\n]*\b0?777\b",
       "World-writable permissions (0777)."),
    _r("SEC-GO-006", "MEDIUM", r"\btemplate\.(?:HTML|JS|URL|CSS)\s*\(",
       "Marks data as safe markup: cross-site scripting if it is user input."),
    _r("SEC-GO-007", "MEDIUM", r"MinVersion\s*:\s*tls\.Version(?:SSL30|TLS10|TLS11)", "Obsolete minimum TLS version."),
    _r("SEC-GO-008", "LOW", r"\brand\.(?:Int|Intn|Int63|Int31|Read|Seed)\w*\s*\(",
       "math/rand is not cryptographically secure: use crypto/rand.", requires=_TOKEN_CONTEXT),
)

# -- Java / Kotlin -----------------------------------------------------------------------------

_JAVA = (
    _r("SEC-JAVA-001", "HIGH",
       r"""Runtime\.getRuntime\(\)\.exec\s*\(|new\s+ProcessBuilder\s*\([^)\n]*["'](?:sh|bash|cmd(?:\.exe)?)["']""",
       "OS command execution: command injection if any part is user data."),
    _r("SEC-JAVA-002", "HIGH", r"\bnew\s+ObjectInputStream\s*\(", "Java deserialization of untrusted data can run code."),
    _r("SEC-JAVA-003", "LOW", r"""MessageDigest\.getInstance\s*\(\s*["'](?:MD5|SHA-?1)["']""", "Weak hash algorithm (MD5/SHA-1)."),
    _r("SEC-JAVA-004", "HIGH",
       r"""\.(?:executeQuery|executeUpdate|execute|createQuery|createNativeQuery|prepareStatement)\s*\([^;\n]*["']\s*\+""",
       "SQL built by string concatenation: use PreparedStatement parameters."),
    _r("SEC-JAVA-005", "HIGH",
       r"""Cipher\.getInstance\s*\(\s*["'](?:DES|DESede|RC4|RC2|Blowfish)\b|["'][A-Za-z0-9]+/ECB/""",
       "Weak or ECB-mode cipher."),
    _r("SEC-JAVA-006", "HIGH",
       r"checkServerTrusted\s*\([^)\n]*\)\s*(?:throws[^{\n]*)?\{\s*\}|ALLOW_ALL_HOSTNAME_VERIFIER|"
       r"setHostnameVerifier\s*\([^)\n]*->\s*true",
       "TLS certificate or hostname verification is disabled."),
    _r("SEC-JAVA-007", "MEDIUM", r"\bnew\s+(?:java\.util\.)?Random\s*\(",
       "java.util.Random is predictable: use SecureRandom.", requires=_TOKEN_CONTEXT),
)

# -- Shell (also applied to Dockerfile RUN lines) --------------------------------------------------

_SHELL = (
    _r("SEC-SH-001", "HIGH", r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b",
       "Downloads and runs a script in one step (curl | sh): nothing is checked before it runs."),
    _r("SEC-SH-002", "MEDIUM", r"\bchmod\s+(?:-\w+\s+)*(?:0?777|a\+rwx)\b", "World-writable permissions (777)."),
    _r("SEC-SH-003", "HIGH", r"""\beval\s+["']?\$""", "eval of a variable: command injection."),
    _r("SEC-SH-004", "MEDIUM", r"\bcurl\b[^\n|;]*\s(?:-k|--insecure)\b|\bwget\b[^\n|;]*--no-check-certificate",
       "TLS certificate verification is turned off."),
    _r("SEC-SH-005", "HIGH", r"\brm\s+-[A-Za-z]*[rR][A-Za-z]*\s+(?:--no-preserve-root\s+)?/(?:\s|\*|$)",
       "rm -r on the filesystem root."),
    _r("SEC-SH-006", "HIGH", r"\|\s*sudo\s+-S\b", "A password piped into sudo -S.", withhold=True),
)

# -- Dockerfile ------------------------------------------------------------------------------

_DOCKER = (
    _r("SEC-DOCKER-001", "MEDIUM", r"^\s*USER\s+(?:root|0)(?::\S+)?\s*$", "The image runs as root.", flags=re.I),
    _r("SEC-DOCKER-004", "MEDIUM", r"^\s*ADD\s+https?://", "ADD from a URL is unverified: use curl with a checksum.", flags=re.I),
    _r("SEC-DOCKER-006", "HIGH",
       r"""^\s*(?:ENV|ARG)\s+\w*(?:PASSWORD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY)\w*(?:\s+|\s*=\s*)(?!["']?\$)["']?[^\s"'][^\n]{5,}""",
       "A credential baked into the image layer (ENV/ARG).", flags=re.I, masks=True),
    _r("SEC-DOCKER-007", "LOW", r"^\s*EXPOSE\s+22\b", "SSH port exposed.", flags=re.I),
)


def _docker_no_user(lines: Sequence[str]) -> list[tuple[int, str]]:
    users = [i for i, ln in enumerate(lines, 1) if re.match(r"\s*USER\s+\S+", ln, re.I)]
    froms = [i for i, ln in enumerate(lines, 1) if re.match(r"\s*FROM\s+\S+", ln, re.I)]
    if not froms:
        return []
    last_from = froms[-1]
    if any(u > last_from for u in users):
        return []
    return [(last_from, lines[last_from - 1].strip())]


def _docker_floating_tag(lines: Sequence[str]) -> list[tuple[int, str]]:
    stages: set[str] = set()
    hits: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, 1):
        m = re.match(r"\s*FROM\s+(?:--\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", ln, re.I)
        if not m:
            continue
        image, alias = m.group(1), m.group(2)
        if alias:
            stages.add(alias.lower())
        if image.lower() == "scratch" or image.lower() in stages or "$" in image or "@sha256:" in image:
            continue
        name = image.rsplit("/", 1)[-1]
        if ":" not in name or name.endswith(":latest"):
            hits.append((i, ln.strip()))
    return hits


_DOCKER_FILE_RULES = (
    FileRule("SEC-DOCKER-002", "MEDIUM", "No USER instruction in the final stage: the container runs as root.", _docker_no_user),
    FileRule("SEC-DOCKER-003", "LOW", "Base image is untagged or ':latest': builds are not reproducible.", _docker_floating_tag),
)

# -- YAML: Kubernetes, Compose, GitHub Actions ----------------------------------------------------

_YAML = (
    _r("SEC-K8S-001", "HIGH", r"^\s*(?:-\s+)?privileged\s*:\s*true\b", "Privileged container."),
    _r("SEC-K8S-002", "HIGH", r"^\s*(?:-\s+)?allowPrivilegeEscalation\s*:\s*true\b", "Privilege escalation allowed."),
    _r("SEC-K8S-003", "HIGH", r"^\s*(?:-\s+)?host(?:Network|PID|IPC)\s*:\s*true\b", "Shares a host namespace (network, PID or IPC)."),
    _r("SEC-K8S-004", "MEDIUM", r"^\s*(?:-\s+)?(?:runAsUser\s*:\s*0\s*$|runAsNonRoot\s*:\s*false\b)", "Runs as root."),
    _r("SEC-K8S-005", "MEDIUM", r"^\s*(?:-\s+)?hostPath\s*:", "Mounts a path from the host."),
    _r("SEC-K8S-006", "HIGH", r"/var/run/docker\.sock", "Mounts the Docker socket: full control of the host."),
    _r("SEC-K8S-009", "MEDIUM", r"""^\s*network_mode\s*:\s*["']?host\b""", "Container shares the host network."),
    _r("SEC-K8S-010", "LOW", r"^\s*readOnlyRootFilesystem\s*:\s*false\b", "Writable root filesystem."),
    _r("SEC-YAML-TLS", "MEDIUM",
       r"^\s*(?:verify_ssl|ssl_verify|verify_tls|tls_verify)\s*:\s*false\b|^\s*insecure(?:_skip_tls_verify)?\s*:\s*true\b",
       "TLS verification is turned off.", flags=re.I),
    _r("SEC-GHA-002", "MEDIUM", r"^\s*pull_request_target\s*:?",
       "pull_request_target runs with a write token and secrets: never check out or run the pull request's code."),
    _r("SEC-GHA-003", "LOW", r"^\s*-?\s*uses\s*:\s*[\w.\-]+/[\w.\-/]+@(?:main|master|latest)\b",
       "An action pinned to a moving branch: pin a commit SHA."),
)

_GHA_EXPR = re.compile(
    r"\$\{\{\s*github\.(?:head_ref|event\.(?:issue|pull_request|comment|review|review_comment|head_commit|"
    r"commits|pages|discussion)[\w.\[\]*]*\.(?:title|body|message|label|ref|name|email|head_ref))\s*\}\}")
_GHA_SAFE = re.compile(r"^\s*[\w\-]+\s*:\s*\$\{\{[^}]*\}\}\s*$")


def _gha_injection(lines: Sequence[str]) -> list[tuple[int, str]]:
    return [(i, ln.strip()) for i, ln in enumerate(lines, 1)
            if _GHA_EXPR.search(ln) and not _GHA_SAFE.match(ln)]


def _yaml_capabilities(lines: Sequence[str]) -> list[tuple[int, str]]:
    """``- SYS_ADMIN`` / ``- ALL`` inside a ``cap_add:`` or ``capabilities: add:`` list (``drop`` is fine)."""
    hits: list[tuple[int, str]] = []
    dangerous = re.compile(r"^\s*-\s*[\"']?(?:ALL|SYS_ADMIN|NET_ADMIN|SYS_PTRACE|SYS_MODULE|DAC_READ_SEARCH)[\"']?\s*$", re.I)
    inline = re.compile(r"\b(?:add|cap_add)\s*:\s*\[[^\]]*\b(?:ALL|SYS_ADMIN|NET_ADMIN|SYS_PTRACE|SYS_MODULE)\b", re.I)
    in_add_indent: Optional[int] = None
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip())
        if inline.search(ln):
            hits.append((i, ln.strip()))
        if re.match(r"\s*(?:add|cap_add)\s*:\s*$", ln):
            in_add_indent = indent
            continue
        if in_add_indent is not None:
            if indent < in_add_indent or (indent == in_add_indent and not ln.lstrip().startswith("-")):
                in_add_indent = None
            elif dangerous.match(ln):
                hits.append((i, ln.strip()))
    return hits


def _yaml_floating_image(lines: Sequence[str]) -> list[tuple[int, str]]:
    hits = []
    for i, ln in enumerate(lines, 1):
        m = re.match(r"""\s*(?:-\s+)?image\s*:\s*["']?([^\s"'#]+)""", ln)
        if not m or "$" in m.group(1) or "@sha256:" in m.group(1):
            continue
        name = m.group(1).rsplit("/", 1)[-1]
        if ":" not in name or name.endswith(":latest"):
            hits.append((i, ln.strip()))
    return hits


_YAML_FILE_RULES = (
    FileRule("SEC-GHA-001", "HIGH",
             "Untrusted GitHub event text used directly in a workflow step: script injection. Pass it through env: instead.",
             _gha_injection),
    FileRule("SEC-K8S-007", "HIGH", "A dangerous Linux capability is added to the container.", _yaml_capabilities),
    FileRule("SEC-K8S-008", "LOW", "Image is untagged or ':latest': deployments are not reproducible.", _yaml_floating_image),
)

# -- Terraform -------------------------------------------------------------------------------

_TF = (
    _r("SEC-TF-001", "MEDIUM", r"""cidr_blocks\s*=\s*\[[^\]\n]*["'](?:0\.0\.0\.0/0|::/0)["']""",
       "Open to the whole internet (0.0.0.0/0)."),
    _r("SEC-TF-002", "HIGH", r"""\bacl\s*=\s*["']public-read(?:-write)?["']""", "Public bucket ACL."),
    _r("SEC-TF-003", "HIGH", r"\bpublicly_accessible\s*=\s*true\b", "Database reachable from the internet."),
    _r("SEC-TF-004", "MEDIUM", r"\b(?:encrypted|storage_encrypted|encryption_enabled|enable_encryption)\s*=\s*false\b",
       "Encryption at rest is turned off."),
    _r("SEC-TF-005", "HIGH", r"""["']?(?:Action|actions)["']?\s*[:=]\s*\[?\s*["']\*["']""", "IAM policy allows every action."),
    _r("SEC-TF-006", "MEDIUM", r"\benable_key_rotation\s*=\s*false\b", "Key rotation is turned off."),
    _r("SEC-TF-007", "MEDIUM",
       r"\b(?:ssl_enforcement_enabled|enable_https_traffic_only|https_only|require_ssl|enforce_ssl|"
       r"min_tls_version_enforced)\s*=\s*false\b", "Plain HTTP / unencrypted transport is allowed."),
    _r("SEC-TF-008", "MEDIUM",
       r"\b(?:block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*false\b",
       "S3 public access block is off."),
    _r("SEC-TF-009", "LOW", r"\bdeletion_protection\s*=\s*false\b", "Deletion protection is off."),
)

RULE_PACKS: dict[str, tuple[Rule, ...]] = {
    "python": _PYTHON,
    "javascript": _JS,
    "go": _GO,
    "java": _JAVA,
    "shell": _SHELL,
    "dockerfile": _DOCKER + _SHELL,
    "yaml": _YAML,
    "terraform": _TF,
}
_FILE_PACKS: dict[str, tuple[FileRule, ...]] = {"dockerfile": _DOCKER_FILE_RULES, "yaml": _YAML_FILE_RULES}

_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "python": ("#",), "shell": ("#",), "dockerfile": ("#",), "yaml": ("#",),
    "javascript": ("//", "/*", "*"), "go": ("//", "/*", "*"), "java": ("//", "/*", "*"),
    "terraform": ("#", "//", "/*", "*"),
}

_BY_SUFFIX = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".ts": "javascript", ".tsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".vue": "javascript", ".svelte": "javascript",
    ".go": "go", ".java": "java", ".kt": "java", ".kts": "java", ".scala": "java",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ksh": "shell",
    ".dockerfile": "dockerfile", ".yaml": "yaml", ".yml": "yaml", ".tf": "terraform", ".tfvars": "terraform",
}


def detect_language(source: str = "", filename: str = "", hint: str = "") -> Optional[str]:
    """The language to apply, or ``None`` when neither a hint, a name nor the text settles it."""
    hint = (hint or "").strip().lower()
    aliases = {"js": "javascript", "ts": "javascript", "typescript": "javascript", "node": "javascript",
               "golang": "go", "kotlin": "java", "bash": "shell", "sh": "shell", "docker": "dockerfile",
               "yml": "yaml", "k8s": "yaml", "kubernetes": "yaml", "hcl": "terraform", "py": "python"}
    hint = aliases.get(hint, hint)
    if hint in LANGUAGES:
        return hint
    name = os.path.basename(filename or "").lower()
    if name.startswith(("dockerfile", "containerfile")) or name.endswith((".dockerfile", ".containerfile")):
        return "dockerfile"
    if name in (".bashrc", ".zshrc", ".profile", ".bash_profile"):
        return "shell"
    suffix = os.path.splitext(name)[1]
    if suffix in _BY_SUFFIX:
        return _BY_SUFFIX[suffix]
    return _sniff(source) if source else None


def _sniff(source: str) -> Optional[str]:
    head = source.lstrip()[:4000]
    first = head.splitlines()[0] if head else ""
    if first.startswith("#!"):
        if "python" in first:
            return "python"
        if "node" in first or "deno" in first:
            return "javascript"
        return "shell"
    if re.search(r"^\s*(?:def\s+\w+\s*\(|class\s+\w+.*:\s*$|from\s+[\w.]+\s+import\s|import\s+[\w.]+\s*$)", head, re.M):
        return "python"
    if re.search(r"^\s*FROM\s+\S+", head, re.M | re.I) and re.search(r"^\s*(?:RUN|COPY|CMD|ENTRYPOINT|WORKDIR)\b", head, re.M | re.I):
        return "dockerfile"
    if re.search(r"^\s*(?:resource|provider|variable|module|data|terraform)\s+[\"\w]", head, re.M) and "{" in head:
        return "terraform"
    if re.search(r"^(?:apiVersion|kind|services|jobs|on)\s*:", head, re.M):
        return "yaml"
    if re.search(r"^\s*package\s+\w+\s*$", head, re.M) and re.search(r"\bfunc\s", head):
        return "go"
    if re.search(r"\b(?:public|private|protected)\s+(?:static\s+)?(?:class|void|final)\b|^\s*import\s+java\.", head, re.M):
        return "java"
    if re.search(r"\b(?:const|let|var)\s+\w+\s*=|\bfunction\s*\w*\s*\(|=>\s*[{(]|\brequire\s*\(|^\s*import\s.+\sfrom\s", head, re.M):
        return "javascript"
    if re.search(r"^\s*(?:if\s+\[|for\s+\w+\s+in\b|echo\s|apt(?:-get)?\s+install|curl\s|wget\s)", head, re.M):
        return "shell"
    return None


def line_rules(language: str) -> tuple[Rule, ...]:
    return RULE_PACKS.get(language, ())


def file_rules(language: str) -> tuple[FileRule, ...]:
    return _FILE_PACKS.get(language, ())


def is_comment_line(stripped: str, language: str) -> bool:
    """A whole-line comment in ``language`` (``stripped`` already has its indentation removed)."""
    return stripped.startswith(_COMMENT_PREFIXES.get(language, ("#",)))


def all_rule_ids() -> frozenset[str]:
    """Every rule id in every pack (line rules and file rules)."""
    ids: set[str] = set()
    for pack in RULE_PACKS.values():
        ids.update(r.rule_id for r in pack)
    for fpack in _FILE_PACKS.values():
        ids.update(r.rule_id for r in fpack)
    return frozenset(ids)


def rule_count() -> int:
    """Distinct rule ids across every pack (line rules and file rules)."""
    return len(all_rule_ids())


def iter_rules(languages: Iterable[str] = LANGUAGES) -> Iterable[Rule]:
    for language in languages:
        yield from RULE_PACKS.get(language, ())
