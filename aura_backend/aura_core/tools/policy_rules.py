"""Declarative tool-call rules: an operator-written file the policy engine evaluates per call.

``ToolPolicy`` decides by tool *name*, capability, taint and sensitivity. It cannot say "this
tool may run, but not with *that* argument" -- the Dart daemon has such rules hard-coded for its
own command allowlist, and the server had nothing. This module adds them as data (ADR-041):

.. code-block:: json

    {"rules": [
      {"id": "no-pipe-to-shell", "tool": "run_command", "arg": "command",
       "deny": ["curl .*\\|\\s*sh"]},
      {"tool": "write_file", "arg": "path", "deny": ["^\\\\.github/"], "personas": ["engineer"]},
      {"tool": "run_command", "arg": "command", "allow_only": ["pytest( .*)?", "ruff .*"],
       "on_mismatch": "confirm", "sensitivities": ["SYSTEM"]}
    ]}

**A rule can only make a call stricter.** Its verdict is *deny* or *require confirmation*; there
is no ``allow``. The engine runs a rule set before the existing checks and a rule that does not
match changes nothing, so an empty or absent file leaves every decision as it was. A rule's
*confirm* is an extra gate on top of whatever the base policy says, never a substitute for it.

**Validated at start-up, loudly.** Every problem is reported with the rule's index
(``rule[3]: ...``); unknown keys are errors (a typo such as ``"denny"`` must not become a rule
that silently does nothing). The file is JSON, or YAML (``.yml``/``.yaml``, ``safe_load`` only).

**Regexes cannot hang the gateway.** Python's ``re`` has no timeout and a thread cannot be
cancelled, so safety is structural: a pattern is refused if it is long, uses a back-reference,
nests an unbounded repeat inside another repeat, repeats an ambiguous alternation
(``(a|aa)+``), or has more than a handful of unbounded repeats (quadratic scans). At call time a
value longer than the rule's ``max_value_chars`` (default 4096) is not scanned at all -- it
*fails closed*: a ``deny``/``confirm`` rule asks for confirmation, an ``allow_only`` rule treats
it as a mismatch.

Matching: ``deny`` and ``confirm`` use ``re.search``; ``allow_only`` uses ``re.fullmatch`` (a
search would let ``pytest; rm -rf /`` satisfy ``pytest``). An argument named ``path`` (or ending
in ``_path``) is matched in both its raw and its normalised spelling (``./a/../.github/x`` ->
``.github/x``), so a leading ``./`` is not a way round ``^\\.github/``. Backslashes are folded to
``/`` for that comparison.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfigurationError

try:  # Python 3.11+ moved the parser; 3.10 keeps the old name.
    from re import _constants as _sre_constants  # type: ignore[attr-defined]
    from re import _parser as _sre_parse  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - older interpreters
    import sre_constants as _sre_constants  # type: ignore[no-redef]
    import sre_parse as _sre_parse  # type: ignore[no-redef]

__all__ = [
    "PolicyRule",
    "PolicyRuleSet",
    "RuleVerdict",
    "load_policy_rules",
    "parse_policy_rules",
    "MAX_PATTERN_CHARS",
    "DEFAULT_MAX_VALUE_CHARS",
]

#: Longest accepted pattern. A rule is a sentence, not a program.
MAX_PATTERN_CHARS = 256
#: Default for ``max_value_chars``; the ceiling a rule may raise it to is below.
DEFAULT_MAX_VALUE_CHARS = 4096
_MAX_VALUE_CHARS_CEILING = 100_000
#: Patterns per rule, rules per file, and the most unbounded repeats one pattern may carry.
_MAX_PATTERNS_PER_RULE = 50
_MAX_RULES = 500
_MAX_UNBOUNDED_REPEATS = 3
_MAX_FILE_BYTES = 256_000
#: Elements of a list argument examined per call (a list longer than this fails closed).
_MAX_LIST_ITEMS = 200

_ALLOWED_KEYS = frozenset({
    "id", "tool", "arg", "deny", "confirm", "allow_only", "on_mismatch", "action", "reason",
    "personas", "sensitivities", "host_routed", "max_value_chars", "case_insensitive",
})
_SENSITIVITY_NAMES = ("PUBLIC", "PERSONAL", "CONFIDENTIAL", "SYSTEM")
_TOOL_NAME = re.compile(r"[a-z0-9_*?\[\]]+")
_ARG_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\])?)*")


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    """What one matching rule asks for: ``deny`` or ``confirm`` -- never anything looser."""

    action: str
    rule_id: str
    detail: str

    @property
    def reason(self) -> str:
        return f"policy_rule_{self.action}:{self.rule_id}"


class _Unsafe(Exception):
    """A pattern that could take unbounded time (message says why)."""


def _check_pattern(pattern: str) -> None:
    """Raise :class:`_Unsafe` for a regex that can backtrack catastrophically or scan quadratically."""
    try:
        parsed = _sre_parse.parse(pattern)  # a syntax error propagates as re.error to _compile
    except (RecursionError, OverflowError) as exc:
        raise _Unsafe(f"too deeply nested ({type(exc).__name__})") from exc
    maxrepeat = _sre_constants.MAXREPEAT
    repeat_ops = {_sre_constants.MAX_REPEAT, _sre_constants.MIN_REPEAT}
    possessive = getattr(_sre_constants, "POSSESSIVE_REPEAT", None)
    if possessive is not None:
        repeat_ops.add(possessive)
    unbounded = 0

    def walk(subpattern: Any, inside_repeat: bool) -> None:
        nonlocal unbounded
        for op, arg in subpattern:
            if op in (_sre_constants.GROUPREF, getattr(_sre_constants, "GROUPREF_EXISTS", None)):
                raise _Unsafe("back-references are not allowed")
            if op in repeat_ops:
                low, high, body = arg
                if high == maxrepeat:
                    unbounded += 1
                multiple = high > 1
                if multiple and _contains_repeat(body, multiple_only=True):
                    raise _Unsafe("a repeat nested inside another repeat can backtrack catastrophically")
                if multiple and _ambiguous_alternation(body):
                    raise _Unsafe("an ambiguous alternation inside a repeat can backtrack catastrophically")
                walk(body, True)
            elif op is _sre_constants.BRANCH:
                for alternative in arg[1]:
                    walk(alternative, inside_repeat)
            elif op in (_sre_constants.SUBPATTERN,):
                walk(arg[-1], inside_repeat)
            elif op in (_sre_constants.ASSERT, _sre_constants.ASSERT_NOT):
                walk(arg[1], inside_repeat)
            elif op is getattr(_sre_constants, "ATOMIC_GROUP", object()):
                walk(arg, inside_repeat)

    def _contains_repeat(subpattern: Any, *, multiple_only: bool) -> bool:
        for op, arg in subpattern:
            if op in repeat_ops:
                _low, high, body = arg
                if high > 1 or not multiple_only:
                    return True
                if _contains_repeat(body, multiple_only=multiple_only):
                    return True
            elif op is _sre_constants.BRANCH:
                if any(_contains_repeat(alt, multiple_only=multiple_only) for alt in arg[1]):
                    return True
            elif op is _sre_constants.SUBPATTERN:
                if _contains_repeat(arg[-1], multiple_only=multiple_only):
                    return True
            elif op in (_sre_constants.ASSERT, _sre_constants.ASSERT_NOT):
                if _contains_repeat(arg[1], multiple_only=multiple_only):
                    return True
        return False

    def _ambiguous_alternation(subpattern: Any) -> bool:
        for op, arg in subpattern:
            if op is _sre_constants.BRANCH:
                literals: list[tuple[int, ...]] = []
                for alt in arg[1]:
                    if all(o is _sre_constants.LITERAL for o, _ in alt) and len(alt) > 0:
                        literals.append(tuple(a for _, a in alt))
                    else:
                        return True  # non-literal alternatives: cannot prove they are disjoint
                for i, left in enumerate(literals):
                    for j, right in enumerate(literals):
                        if i != j and right[: len(left)] == left:
                            return True  # one alternative is a prefix of (or equal to) another
            elif op is _sre_constants.SUBPATTERN:
                if _ambiguous_alternation(arg[-1]):
                    return True
            elif op in repeat_ops:
                if _ambiguous_alternation(arg[2]):
                    return True
        return False

    walk(parsed, False)
    if unbounded > _MAX_UNBOUNDED_REPEATS:
        raise _Unsafe(
            f"{unbounded} unbounded repeats (* + {{n,}}); at most {_MAX_UNBOUNDED_REPEATS} are "
            "allowed because each one multiplies the scanning cost"
        )


def _compile(pattern: Any, *, where: str, flags: int, errors: list[str]) -> "re.Pattern[str] | None":
    if not isinstance(pattern, str) or not pattern:
        errors.append(f"{where}: pattern must be a non-empty string")
        return None
    if len(pattern) > MAX_PATTERN_CHARS:
        errors.append(f"{where}: pattern is {len(pattern)} characters; the limit is {MAX_PATTERN_CHARS}")
        return None
    try:
        _check_pattern(pattern)
        return re.compile(pattern, flags)
    except _Unsafe as exc:
        errors.append(f"{where}: unsafe pattern {pattern!r}: {exc}")
    except (re.error, RecursionError, OverflowError) as exc:
        errors.append(f"{where}: pattern {pattern!r} does not compile ({exc})")
    return None


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One validated rule. Build through :func:`parse_policy_rules`, never by hand."""

    rule_id: str
    tool: str
    arg: str | None
    deny: tuple["re.Pattern[str]", ...]
    confirm: tuple["re.Pattern[str]", ...]
    allow_only: tuple["re.Pattern[str]", ...]
    on_mismatch: str
    action: str | None
    reason: str
    personas: frozenset[str]
    sensitivities: frozenset[str]
    host_routed: bool | None
    max_value_chars: int

    def applies(self, tool: str, persona: str | None, sensitivity: str, host_routed: bool) -> bool:
        if not fnmatch.fnmatchcase(tool, self.tool):
            return False
        if self.personas and (persona or "") not in self.personas:
            return False
        if self.sensitivities and sensitivity not in self.sensitivities:
            return False
        if self.host_routed is not None and self.host_routed != host_routed:
            return False
        return True

    def check(self, arguments: Mapping[str, Any]) -> RuleVerdict | None:
        """The verdict for one call's arguments, or ``None`` when this rule does not fire."""
        if self.arg is None:
            # An argument-less rule is "this tool, in this scope": it carries an explicit action.
            return RuleVerdict(self.action or "deny", self.rule_id,
                               self.reason or f"{self.tool} is restricted by policy")
        values, complete = _extract(arguments, self.arg)
        if not complete:
            # A list too long to examine element by element: fail closed, exactly as an
            # oversized string does.
            return RuleVerdict("confirm", self.rule_id,
                               f"{self.arg} has too many entries for rule {self.rule_id} to check")
        for value in values:
            if len(value) > self.max_value_chars:
                if self.deny or self.confirm or self.allow_only:
                    return RuleVerdict(
                        "confirm", self.rule_id,
                        f"{self.arg} is longer than {self.max_value_chars} characters, which "
                        f"rule {self.rule_id} does not scan",
                    )
                continue
            spellings = _spellings(self.arg, value)
            for pattern in self.deny:
                if any(pattern.search(text) for text in spellings):
                    return RuleVerdict("deny", self.rule_id,
                                       self.reason or f"{self.arg} matches a denied pattern")
            if self.allow_only and not any(
                pattern.fullmatch(text) for text in spellings[:1] for pattern in self.allow_only
            ):
                return RuleVerdict(self.on_mismatch, self.rule_id,
                                   self.reason or f"{self.arg} is not on this tool's allow-list")
            for pattern in self.confirm:
                if any(pattern.search(text) for text in spellings):
                    return RuleVerdict("confirm", self.rule_id,
                                       self.reason or f"{self.arg} matches a pattern that needs approval")
        return None


def _spellings(arg: str, value: str) -> list[str]:
    """``value`` as written, plus its normalised path form when the argument names a path."""
    leaf = arg.rsplit(".", 1)[-1].removesuffix("[]")
    if leaf != "path" and not leaf.endswith("_path"):
        return [value]
    folded = value.replace("\\", "/")
    normalised = posixpath.normpath(folded) if folded else folded
    normalised = normalised.lstrip("/") if normalised.startswith("/") else normalised
    spellings = [value]
    for candidate in (folded, normalised):
        if candidate not in spellings:
            spellings.append(candidate)
    return spellings


def _extract(arguments: Mapping[str, Any], path: str) -> tuple[list[str], bool]:
    """Every string reachable at ``path`` (``a.b``, ``ops[].path``), and whether all were reached.

    A value that is not a string is examined as its JSON text, so a rule on a numeric or boolean
    argument still sees it. A missing argument yields nothing (the rule does not fire).
    """
    nodes: list[Any] = [arguments]
    complete = True
    for segment in path.split("."):
        iterate = segment.endswith("[]")
        key = segment.removesuffix("[]")
        following: list[Any] = []
        for node in nodes:
            if not isinstance(node, Mapping) or key not in node:
                continue
            child = node[key]
            if iterate:
                if not isinstance(child, (list, tuple)):
                    continue
                if len(child) > _MAX_LIST_ITEMS:
                    complete = False
                    child = child[:_MAX_LIST_ITEMS]
                following.extend(child)
            else:
                following.append(child)
        nodes = following
    out: list[str] = []
    for node in nodes:
        if node is None:
            continue
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, (list, tuple)):
            if len(node) > _MAX_LIST_ITEMS:
                complete = False
            out.extend(item if isinstance(item, str) else _json_text(item) for item in node[:_MAX_LIST_ITEMS])
        else:
            out.append(_json_text(node))
    return out, complete


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        return str(type(value).__name__)


class PolicyRuleSet:
    """An ordered, immutable collection of validated rules."""

    def __init__(self, rules: Sequence[PolicyRule] = (), *, source: str = "") -> None:
        self._rules = tuple(rules)
        self.source = source

    def __len__(self) -> int:
        return len(self._rules)

    def __bool__(self) -> bool:
        return bool(self._rules)

    @property
    def rules(self) -> tuple[PolicyRule, ...]:
        return self._rules

    def evaluate(
        self,
        tool: str,
        arguments: Mapping[str, Any] | None,
        *,
        persona: str | None,
        sensitivity: str,
        host_routed: bool,
    ) -> RuleVerdict | None:
        """The strictest verdict any rule gives this call: a ``deny`` anywhere beats a ``confirm``.

        ``arguments=None`` means the caller did not pass them (no registry path does); a rule that
        needs an argument then asks for confirmation rather than silently not firing.
        """
        confirm: RuleVerdict | None = None
        for rule in self._rules:
            if not rule.applies(tool, persona, sensitivity, host_routed):
                continue
            if arguments is None and rule.arg is not None:
                verdict: RuleVerdict | None = RuleVerdict(
                    "confirm", rule.rule_id, "the call's arguments were not available to the rule")
            else:
                verdict = rule.check(arguments or {})
            if verdict is None:
                continue
            if verdict.action == "deny":
                return verdict
            confirm = confirm or verdict
        return confirm


# -- loading and validation ---------------------------------------------------------------


def load_policy_rules(path: str) -> PolicyRuleSet:
    """Read, parse and validate the file at ``path``. Raises :class:`ConfigurationError` listing
    every problem found (not just the first), each prefixed with its rule's index."""
    if not path:
        return PolicyRuleSet()
    file = Path(path)
    try:
        size = file.stat().st_size
    except OSError as exc:
        raise ConfigurationError(
            f"AURA_TOOL_POLICY_FILE {path!r} cannot be read: {exc.strerror or exc}",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        ) from exc
    if size > _MAX_FILE_BYTES:
        raise ConfigurationError(
            f"AURA_TOOL_POLICY_FILE {path!r} is {size} bytes; the limit is {_MAX_FILE_BYTES}",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        )
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(
            f"AURA_TOOL_POLICY_FILE {path!r} cannot be read: {exc}",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        ) from exc
    try:
        if file.suffix.lower() in (".yml", ".yaml"):
            import yaml  # already a runtime dependency (connector OpenAPI descriptions)

            document = yaml.safe_load(text)
        else:
            document = json.loads(text)
    except Exception as exc:  # json.JSONDecodeError, yaml.YAMLError, RecursionError
        raise ConfigurationError(
            f"AURA_TOOL_POLICY_FILE {path!r} is not valid {'YAML' if file.suffix.lower() in ('.yml', '.yaml') else 'JSON'}: {exc}",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        ) from exc
    return parse_policy_rules(document, source=path)


def parse_policy_rules(document: Any, *, source: str = "<inline>") -> PolicyRuleSet:
    """Validate an already-parsed document (a list of rules, or ``{"rules": [...]}``)."""
    if isinstance(document, Mapping):
        extra = sorted(set(document) - {"rules", "version"})
        if extra:
            raise ConfigurationError(
                f"tool policy {source}: unknown top-level key(s) {extra}; expected 'rules'",
                details={"setting": "AURA_TOOL_POLICY_FILE"},
            )
        raw = document.get("rules")
    else:
        raw = document
    if not isinstance(raw, list):
        raise ConfigurationError(
            f"tool policy {source}: expected a list of rules (or an object with a 'rules' list)",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        )
    if len(raw) > _MAX_RULES:
        raise ConfigurationError(
            f"tool policy {source}: {len(raw)} rules; the limit is {_MAX_RULES}",
            details={"setting": "AURA_TOOL_POLICY_FILE"},
        )
    errors: list[str] = []
    rules: list[PolicyRule] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        rule = _parse_rule(index, entry, errors, seen_ids)
        if rule is not None:
            rules.append(rule)
    if errors:
        raise ConfigurationError(
            f"tool policy {source} is invalid:\n  " + "\n  ".join(errors),
            details={"setting": "AURA_TOOL_POLICY_FILE", "problems": errors},
        )
    return PolicyRuleSet(rules, source=source)


def _string_list(value: Any, where: str, errors: list[str], *, upper: bool = False) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        errors.append(f"{where}: must be a list of non-empty strings")
        return None
    return [v.upper() for v in value] if upper else list(value)


def _parse_rule(index: int, entry: Any, errors: list[str], seen_ids: set[str]) -> PolicyRule | None:
    at = f"rule[{index}]"
    before = len(errors)
    if not isinstance(entry, Mapping):
        errors.append(f"{at}: must be an object")
        return None
    unknown = sorted(set(entry) - _ALLOWED_KEYS)
    if unknown:
        errors.append(f"{at}: unknown key(s) {unknown}; allowed: {sorted(_ALLOWED_KEYS)}")
    rule_id = entry.get("id", f"rule-{index}")
    if not isinstance(rule_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", rule_id):
        errors.append(f"{at}: 'id' must be 1-64 characters of letters, digits and _ . : -")
        rule_id = f"rule-{index}"
    elif rule_id in seen_ids:
        errors.append(f"{at}: duplicate id {rule_id!r}")
    seen_ids.add(rule_id)

    tool = entry.get("tool")
    if not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool):
        errors.append(f"{at}: 'tool' must be a tool name (lowercase letters, digits, _; * and ? wildcards allowed)")
        tool = ""

    arg = entry.get("arg")
    if arg is not None and (not isinstance(arg, str) or not _ARG_PATH.fullmatch(arg) or len(arg) > 100):
        errors.append(f"{at}: 'arg' must be an argument name, optionally dotted or with [] for lists "
                      "(e.g. 'path', 'operations[].path')")
        arg = None

    flags = re.IGNORECASE if entry.get("case_insensitive") is True else 0
    if "case_insensitive" in entry and not isinstance(entry["case_insensitive"], bool):
        errors.append(f"{at}: 'case_insensitive' must be true or false")

    compiled: dict[str, list[re.Pattern[str]]] = {}
    for key in ("deny", "confirm", "allow_only"):
        patterns = _string_list(entry.get(key), f"{at}.{key}", errors)
        if patterns is None:
            compiled[key] = []
            continue
        if len(patterns) > _MAX_PATTERNS_PER_RULE:
            errors.append(f"{at}.{key}: {len(patterns)} patterns; the limit is {_MAX_PATTERNS_PER_RULE}")
            compiled[key] = []
            continue
        compiled[key] = [
            p for i, raw in enumerate(patterns)
            if (p := _compile(raw, where=f"{at}.{key}[{i}]", flags=flags, errors=errors)) is not None
        ]
    has_patterns = bool(entry.get("deny") or entry.get("confirm") or entry.get("allow_only"))

    action = entry.get("action")
    if action is not None and action not in ("deny", "confirm"):
        errors.append(f"{at}: 'action' must be \"deny\" or \"confirm\" (a rule can only tighten)")
        action = None
    if arg is None and entry.get("arg") is None:
        if has_patterns:
            errors.append(f"{at}: patterns need an 'arg' to match against")
        if action is None:
            errors.append(f"{at}: a rule without 'arg' needs an explicit 'action' (\"deny\" or \"confirm\")")
    else:
        if not has_patterns:
            errors.append(f"{at}: 'arg' given but none of 'deny', 'confirm' or 'allow_only'")
        if action is not None:
            errors.append(f"{at}: 'action' only applies to a rule without 'arg'")
    mismatch = entry.get("on_mismatch", "deny")
    if mismatch not in ("deny", "confirm"):
        errors.append(f"{at}: 'on_mismatch' must be \"deny\" or \"confirm\"")
        mismatch = "deny"
    if "on_mismatch" in entry and not entry.get("allow_only"):
        errors.append(f"{at}: 'on_mismatch' only applies with 'allow_only'")

    reason = entry.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 300:
        errors.append(f"{at}: 'reason' must be a string of at most 300 characters")
        reason = ""

    personas = _string_list(entry.get("personas"), f"{at}.personas", errors) or []
    sens = _string_list(entry.get("sensitivities"), f"{at}.sensitivities", errors, upper=True) or []
    for name in sens:
        if name not in _SENSITIVITY_NAMES:
            errors.append(f"{at}.sensitivities: {name!r} is not one of {list(_SENSITIVITY_NAMES)}")

    routed = entry.get("host_routed")
    if routed is not None and not isinstance(routed, bool):
        errors.append(f"{at}: 'host_routed' must be true or false")
        routed = None

    limit = entry.get("max_value_chars", DEFAULT_MAX_VALUE_CHARS)
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= _MAX_VALUE_CHARS_CEILING):
        errors.append(f"{at}: 'max_value_chars' must be an integer from 1 to {_MAX_VALUE_CHARS_CEILING}")
        limit = DEFAULT_MAX_VALUE_CHARS

    if len(errors) > before:
        return None
    return PolicyRule(
        rule_id=rule_id, tool=tool, arg=arg,
        deny=tuple(compiled["deny"]), confirm=tuple(compiled["confirm"]),
        allow_only=tuple(compiled["allow_only"]), on_mismatch=mismatch, action=action,
        reason=reason, personas=frozenset(personas), sensitivities=frozenset(sens),
        host_routed=routed, max_value_chars=limit,
    )
