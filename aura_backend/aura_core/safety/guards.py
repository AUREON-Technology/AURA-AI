"""Individual guards (ADR-006).

Each guard is small, pure, and independently testable: it takes text and returns a
:class:`GuardVerdict`. It never mutates, never decides whether the turn proceeds, and never
raises. Aggregation and enforcement belong to :mod:`aura_core.safety.pipeline`.

Detection here is rule-based. That is a deliberate starting point, not a claim of completeness:
a trained classifier needs labelled data the project does not yet have, and rules ship today
while the guard results accumulate exactly the dataset needed to train one later
(``SafetyTriggered`` events, ADR-006). ``classifiers.py`` is the seam for that upgrade.

Every threshold is a constructor argument so a deployment can tune false positives without
forking detection logic.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from typing import Iterable, Sequence

from ..domain.safety import GuardAction, GuardStage, GuardVerdict, Span, redact_spans
from ..text import ARABIC_STOPWORDS, arabic_view, fold, is_arabic_token, light_stem

__all__ = [
    "Guard",
    "SizeGuard",
    "EncodingGuard",
    "PromptInjectionGuard",
    "JailbreakGuard",
    "PIIGuard",
    "SecretGuard",
    "SystemPromptLeakGuard",
    "GroundednessGuard",
    "default_input_guards",
    "default_context_guards",
    "default_output_guards",
]


# --------------------------------------------------------------------------------------
# Arabic matching (EV-7 finding: no guard had an Arabic pattern, so an Arabic injection or
# jailbreak passed untouched while the product ships an Arabic UI and Arabic speech)
# --------------------------------------------------------------------------------------

#: Harakat, Quranic marks, superscript alef and tatweel: optional in written Arabic, so a pattern
#: must not depend on whether the writer used them. The folding itself (and this same class) lives
#: in ``aura_core/text/arabic.py``, shared with retrieval so a guard and search agree on what
#: counts as the same word; this copy only extends a match's span over trailing marks.
_ARABIC_STRIP = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")

#: ``text`` with diacritics removed and letter variants folded, plus each kept character's index
#: in ``text`` (so a match maps back to an accurate span). ``None`` if there is no Arabic.
_arabic_view = arabic_view


def _scan(
    text: str,
    patterns: Sequence[tuple[re.Pattern[str], float, str]],
    arabic_patterns: Sequence[tuple[re.Pattern[str], float, str]],
) -> tuple[list[Span], dict[str, float]]:
    """Run ``patterns`` on ``text`` and ``arabic_patterns`` on its normalised Arabic view.

    Both families share labels, and only the strongest weight per label counts, so the same
    signal written in two languages is one signal, not two.
    """
    spans: list[Span] = []
    weights: dict[str, float] = {}
    for pattern, weight, label in patterns:
        match = pattern.search(text)
        if match:
            spans.append(Span(match.start(), match.end(), label, match.group(0)[:80]))
            weights[label] = max(weights.get(label, 0.0), weight)
    view = _arabic_view(text) if arabic_patterns else None
    if view is not None:
        normalised, index = view
        for pattern, weight, label in arabic_patterns:
            match = pattern.search(normalised)
            if match:
                start, end = index[match.start()], index[match.end() - 1] + 1
                while end < len(text) and _ARABIC_STRIP.match(text[end]):
                    end += 1  # keep the last letter's own diacritics inside the span
                spans.append(Span(start, end, label, text[start:end][:80]))
                weights[label] = max(weights.get(label, 0.0), weight)
    return spans, weights


#: Optional conjunction prefix (wa-/fa-) written attached to the next word.
_AR_CONJ = r"(?<!\w)[وف]?"


class Guard:
    """Base class. Subclasses implement :meth:`_evaluate`.

    The wrapper times every evaluation and converts an unexpected exception into an ``ALLOW``
    with a ``guard_error`` reason. A crashing guard must not take down the turn -- but it must
    also be visible, which is why the reason is recorded rather than swallowed.
    """

    name: str = "guard"
    stages: tuple[GuardStage, ...] = (GuardStage.INPUT,)

    def applies_to(self, stage: GuardStage) -> bool:
        return stage in self.stages

    def evaluate(self, text: str, *, stage: GuardStage, context: dict | None = None) -> GuardVerdict:
        started = time.perf_counter()
        try:
            verdict = self._evaluate(text, stage=stage, context=context or {})
        except Exception as exc:  # pragma: no cover - defensive
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.ALLOW,
                reason="guard_error",
                detail=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        object.__setattr__(verdict, "duration_ms", (time.perf_counter() - started) * 1000.0)
        return verdict

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        raise NotImplementedError


# --------------------------------------------------------------------------------------
# Structural guards
# --------------------------------------------------------------------------------------


class SizeGuard(Guard):
    """Rejects pathological input before anything expensive touches it.

    First in the chain deliberately: regex scanning a 10 MB "message" is itself a denial-of-
    service, so the cheapest check has to run first.
    """

    name = "size"
    stages = (GuardStage.INPUT, GuardStage.CONTEXT, GuardStage.TOOL_RESULT)

    def __init__(self, *, max_chars: int = 32_000, max_line_chars: int = 8_000) -> None:
        self._max_chars = max_chars
        self._max_line = max_line_chars

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        if len(text) > self._max_chars:
            # Context and tool results are truncated rather than rejected: dropping a long
            # document entirely is a worse outcome than using its first N characters.
            if stage in (GuardStage.CONTEXT, GuardStage.TOOL_RESULT):
                return GuardVerdict(
                    guard=self.name,
                    action=GuardAction.REDACT,
                    reason="too_long",
                    sanitised=text[: self._max_chars] + "\n[…truncated by size guard]",
                    detail=f"{len(text)} chars exceeds {self._max_chars}",
                )
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.BLOCK,
                reason="too_long",
                detail=f"{len(text)} chars exceeds {self._max_chars}",
            )
        longest = max((len(line) for line in text.splitlines()), default=0)
        if longest > self._max_line:
            # A single enormous line is the signature of an encoded payload rather than prose.
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.FLAG,
                reason="long_line",
                confidence=0.5,
                detail=f"longest line is {longest} chars",
            )
        return GuardVerdict.allow(self.name)


class EncodingGuard(Guard):
    """Strips invisible and direction-controlling characters.

    Zero-width characters and bidirectional overrides let an attacker write text that renders
    one way to a human reviewer and tokenises another way to the model -- the "Trojan Source"
    class of attack. Homoglyph-heavy text is flagged rather than stripped, since legitimate
    multilingual content uses non-Latin scripts.
    """

    name = "encoding"
    stages = (GuardStage.INPUT, GuardStage.CONTEXT, GuardStage.TOOL_RESULT)

    # Explicit escapes rather than literal characters: these are invisible in an editor, so
    # a literal class is unreviewable and one stray edit silently disables the guard.
    _INVISIBLE = re.compile(
        "["
        "\u200b-\u200f"  # zero-width space/non-joiner/joiner, LTR/RTL marks
        "\u202a-\u202e"  # bidirectional embedding/override (Trojan Source)
        "\u2060-\u2064"  # word joiner, invisible operators
        "\u206a-\u206f"  # deprecated formatting controls
        "\ufeff"          # zero-width no-break space / BOM
        "\u00ad"          # soft hyphen
        "]"
    )
    _CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        cleaned = self._INVISIBLE.sub("", text)
        cleaned = self._CONTROL.sub("", cleaned)
        # NFKC folds compatibility forms so "ｉｇｎｏｒｅ" and "ignore" reach later guards as the
        # same string -- without this, every downstream pattern is trivially bypassable.
        normalised = unicodedata.normalize("NFKC", cleaned)

        if normalised != text:
            removed = len(text) - len(cleaned)
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.REDACT,
                reason="obfuscated_encoding",
                confidence=0.8 if removed else 0.4,
                sanitised=normalised,
                detail=f"removed {removed} invisible/control chars; NFKC-normalised",
            )
        return GuardVerdict.allow(self.name)


# --------------------------------------------------------------------------------------
# Injection and jailbreak
# --------------------------------------------------------------------------------------


class PromptInjectionGuard(Guard):
    """Detects instruction-shaped content in a data channel.

    This is the guard that closes SEC-02. The critical design point is **stage-dependent
    action**: the same phrase means different things depending on where it appears.

    * In ``INPUT`` (the user's own message) "ignore previous instructions" is at most
      suspicious -- users legitimately say things like that, and they are allowed to direct
      their own assistant. Action: ``FLAG``.
    * In ``CONTEXT`` or ``TOOL_RESULT`` -- a retrieved document, a memory, a web page -- it is
      an attempt by a third party to issue instructions. Action: ``QUARANTINE``, which drops the
      chunk and taints the turn so side-effecting tools require confirmation.

    Scoring is additive across independent signal families with diminishing returns, rather than
    "any pattern matches → block". A single weak signal should not quarantine a legitimate
    document that happens to contain the word "system".
    """

    name = "prompt_injection"
    stages = (GuardStage.INPUT, GuardStage.CONTEXT, GuardStage.TOOL_RESULT)

    # (pattern, weight, label). Weights reflect how rarely the phrasing appears in benign text.
    _PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
        (re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
                    r"(?:instructions?|prompts?|rules?|directions?)\b", re.I), 0.55, "ignore_previous"),
        (re.compile(r"\bdisregard\s+(?:all\s+|the\s+)?(?:previous|prior|above|system)\b", re.I),
         0.5, "disregard"),
        (re.compile(r"\bforget\s+(?:everything|all)\s+(?:you|above|before)\b", re.I), 0.45, "forget_all"),
        (re.compile(r"\byou\s+are\s+now\s+(?:a|an|the)\b", re.I), 0.4, "role_reassignment"),
        (re.compile(r"\bnew\s+(?:instructions?|system\s+prompt|rules?)\s*:", re.I), 0.5, "new_instructions"),
        (re.compile(r"^\s*(?:system|assistant)\s*:", re.I | re.M), 0.4, "role_spoofing"),
        (re.compile(r"<\|?(?:im_start|im_end|system|endoftext)\|?>", re.I), 0.6, "control_token"),
        (re.compile(r"\breveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)\b", re.I),
         0.5, "prompt_extraction"),
        (re.compile(r"\bprint\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)\b", re.I),
         0.5, "prompt_extraction"),
        (re.compile(r"\b(?:call|invoke|execute|run)\s+(?:the\s+)?(?:tool|function|command)\b", re.I),
         0.35, "tool_directive"),
        (re.compile(r"\bdo\s+not\s+(?:tell|inform|mention\s+to)\s+the\s+user\b(?!\s+base\b)", re.I),
         0.6, "concealment"),
        (re.compile(r"\bwithout\s+(?:asking|telling|informing)\s+the\s+user\b(?!\s+base\b)", re.I),
         0.55, "concealment"),
        (re.compile(r"\bsend\s+(?:all|the)\s+.{0,30}\bto\s+https?://", re.I), 0.7, "exfiltration"),
        (re.compile(r"\bAURA-DATA\b|<<<END-AURA-DATA", re.I), 0.75, "fence_escape"),
    )

    #: The same signal families in Arabic, matched against :func:`_arabic_view` (no diacritics;
    #: alef/yeh/teh-marbuta variants folded), with the same weights as their English twins.
    _ARABIC_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
        (re.compile(_AR_CONJ + r"[تا]?تجاهل(?:ي|وا)?\s+(?:جميع\s+|كل\s+|كافه\s+)?(?:ال)?"
                    r"(?:تعليمات|اوامر|توجيهات|قواعد)\s+(?:ال)?(?:سابقه|اعلاه)"), 0.55, "ignore_previous"),
        (re.compile(_AR_CONJ + r"(?:ا)?نس(?:ي|وا)?\s+(?:كل|جميع)\s+(?:ما|شيء)\s+(?:سبق|قبل|اعلاه)"),
         0.45, "forget_all"),
        (re.compile(_AR_CONJ + r"انت(?:ي)?\s+الان\s+(?!في\s)\w"), 0.4, "role_reassignment"),
        (re.compile(r"(?:ال)?تعليمات\s+(?:ال)?جديده\s*:"), 0.5, "new_instructions"),
        (re.compile(_AR_CONJ + r"(?:اكشف|اظهر|اعرض|اطبع)(?:ي)?\s+(?:لي\s+)?(?:\S+\s+)?"
                    r"(?:(?:تعليمات|موجه|اوامر)\s+(?:ال)?نظام|تعليماتك|موجهك)"), 0.5, "prompt_extraction"),
        (re.compile(_AR_CONJ + r"(?:نفذ|شغل|استدع)(?:ي)?\s+(?:ال)?(?:اداه|امر|داله)"), 0.35, "tool_directive"),
        (re.compile(_AR_CONJ + r"(?:لا|دون|بدون)\s+(?:ان\s+)?(?:تخبر|تعلم|تبلغ|اخبار|ابلاغ|اعلام|علم)\s+"
                    r"(?:ال)?مستخدم(?!ين)"), 0.6, "concealment"),
        (re.compile(_AR_CONJ + r"(?:ارسل|انقل)(?:ي)?\s+(?:جميع|كل)\s+.{0,40}?(?:الي|ل)\s*https?://"),
         0.7, "exfiltration"),
    )

    def __init__(self, *, threshold: float = 0.6) -> None:
        self._threshold = threshold

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        # Strongest weight per label, so five variants of "ignore previous" (in either language)
        # do not sum to a false certainty.
        spans, labels = _scan(text, self._PATTERNS, self._ARABIC_PATTERNS)

        if not labels:
            return GuardVerdict.allow(self.name)

        # Diminishing-returns combination: 1 - Π(1 - w). Two independent 0.5 signals give 0.75,
        # not 1.0, so evidence accumulates without a single pattern being able to saturate.
        score = 1.0 - math.prod(1.0 - w for w in labels.values())
        score = round(min(score, 0.99), 4)

        if score < self._threshold:
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.FLAG,
                reason="weak_injection_signal",
                confidence=score,
                spans=tuple(spans),
                detail=f"signals: {sorted(labels)}",
            )

        if stage is GuardStage.INPUT:
            # The user's own message. They may ask their assistant to change behaviour; that is
            # not an attack on anyone else. Recorded, not blocked.
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.FLAG,
                reason="user_instruction_override",
                confidence=score,
                spans=tuple(spans),
                detail=f"signals: {sorted(labels)}",
            )

        return GuardVerdict(
            guard=self.name,
            action=GuardAction.QUARANTINE,
            reason="injection_in_data_channel",
            confidence=score,
            spans=tuple(spans),
            detail=f"signals: {sorted(labels)} in {stage}",
        )


class JailbreakGuard(Guard):
    """Detects framings whose purpose is to disable the assistant's constraints."""

    name = "jailbreak"
    stages = (GuardStage.INPUT,)

    _PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
        (re.compile(r"\bDAN\b.{0,40}\b(?:mode|prompt|jailbreak)\b", re.I), 0.6, "dan"),
        (re.compile(r"\b(?:developer|god|debug|admin)\s+mode\s+(?:enabled|on|activated)\b", re.I),
         0.55, "fake_mode"),
        (re.compile(r"\bpretend\s+(?:you\s+)?(?:are|to\s+be)\s+.{0,40}\bno\s+(?:rules|restrictions|filters)\b",
                    re.I), 0.6, "roleplay_bypass"),
        (re.compile(r"\bwithout\s+any\s+(?:restrictions|filters|guidelines|ethics|limitations)\b", re.I),
         0.55, "unrestricted"),
        (re.compile(r"\byou\s+have\s+no\s+(?:restrictions|guidelines|rules|filters)\b", re.I),
         0.6, "unrestricted"),
        (re.compile(r"\bhypothetically.{0,60}\bhow\s+(?:would|to)\b.{0,40}\b(?:bypass|hack|exploit)\b",
                    re.I), 0.45, "hypothetical_framing"),
        (re.compile(r"\bfor\s+(?:educational|research)\s+purposes\s+only\b.{0,80}"
                    r"\b(?:illegal|weapon|malware|exploit)\b", re.I), 0.4, "pretext"),
    )

    #: Arabic forms of the same framings, against :func:`_arabic_view`.
    _ARABIC_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
        (re.compile(r"(?:تفعيل|تشغيل)\s+وضع\s+(?:ال)?(?:مطور|مطورين|اله|مسوول|مسؤول|مدير)"
                    r"|وضع\s+(?:ال)?(?:مطور|اله|مسوول|مسؤول|مدير)\s+(?:مفعل|نشط|مشغل)"
                    r"|انت(?:ي)?\s+(?:الان\s+)?في\s+وضع\s+(?:ال)?(?:مطور|اله|مسوول|مسؤول|مدير)"), 0.55, "fake_mode"),
        (re.compile(_AR_CONJ + r"تظاهر(?:ي)?\s+(?:ب)?(?:ان|انك|انكي).{0,50}(?:بلا|بدون|دون)\s+(?:اي\s+)?"
                    r"(?:قواعد|قيود|فلاتر|ضوابط)"), 0.6, "roleplay_bypass"),
        (re.compile(_AR_CONJ + r"(?:ليس\s+لديك|لا\s+توجد\s+لديك|لا\s+تملك)\s+(?:اي\s+)?"
                    r"(?:قيود|قواعد|فلاتر|ضوابط)"), 0.6, "unrestricted"),
        # "without restrictions ON <something>" describes a thing, not the assistant.
        (re.compile(r"(?<!\w)(?:بدون|دون|بلا)\s+(?:اي\s+)?(?:قيود|فلاتر|ضوابط)(?!\s+(?:علي|في|ل))"),
         0.55, "unrestricted"),
    )

    def __init__(self, *, threshold: float = 0.7) -> None:
        self._threshold = threshold

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        spans, weights = _scan(text, self._PATTERNS, self._ARABIC_PATTERNS)

        if not weights:
            return GuardVerdict.allow(self.name)

        score = round(min(1.0 - math.prod(1.0 - w for w in weights.values()), 0.99), 4)
        # Never blocks on its own. Detecting an attempt to bypass constraints is useful signal;
        # the constraints themselves are what actually enforce behaviour, and refusing outright
        # on a pattern match makes the assistant unusable for security topics.
        return GuardVerdict(
            guard=self.name,
            action=GuardAction.FLAG,
            reason="jailbreak_attempt" if score >= self._threshold else "weak_jailbreak_signal",
            confidence=score,
            spans=tuple(spans),
            detail=f"signals: {sorted(weights)}",
        )


# --------------------------------------------------------------------------------------
# Data protection
# --------------------------------------------------------------------------------------


class PIIGuard(Guard):
    """Finds and optionally redacts personal data.

    Runs on **output and memory writes**, not on user input: a personal assistant whose user
    says "my number is X" must be able to act on it. What matters is that PII does not leak into
    a third party's answer, and that it is not written into long-term storage without a policy
    decision -- which is what ``services/memory.py`` does today with no controls at all.

    Detection is intentionally conservative. Over-redacting an assistant's own answers is a
    visible product defect, so patterns require structural markers rather than loose digit runs.
    """

    name = "pii"
    stages = (GuardStage.OUTPUT,)

    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "email"),
        (re.compile(r"(?<![\w.])\+?\d{1,3}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}(?![\w.])"),
         "phone"),
        (re.compile(r"\b(?:\d[ \-]?){13,19}\b"), "card"),
        (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "iban"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "ip"),
    )

    def __init__(self, *, redact: bool = True, allow_labels: Sequence[str] = ()) -> None:
        self._redact = redact
        self._allow = frozenset(allow_labels)

    @staticmethod
    def _luhn(digits: str) -> bool:
        """Card numbers are validated with Luhn before redaction.

        Without this, order numbers, ISBNs, and long identifiers are redacted from answers --
        an over-redaction that users notice immediately.
        """
        digits = re.sub(r"\D", "", digits)
        if not 13 <= len(digits) <= 19:
            return False
        total, alternate = 0, False
        for char in reversed(digits):
            value = ord(char) - 48
            if alternate:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
            alternate = not alternate
        return total % 10 == 0

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        spans: list[Span] = []
        for pattern, label in self._PATTERNS:
            if label in self._allow:
                continue
            for match in pattern.finditer(text):
                if label == "card" and not self._luhn(match.group(0)):
                    continue
                spans.append(Span(match.start(), match.end(), label, ""))

        if not spans:
            return GuardVerdict.allow(self.name)

        labels = sorted({s.label for s in spans})
        if not self._redact:
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.FLAG,
                reason="pii_detected",
                confidence=0.8,
                spans=tuple(spans),
                detail=f"labels: {labels}",
            )
        return GuardVerdict(
            guard=self.name,
            action=GuardAction.REDACT,
            reason="pii_redacted",
            confidence=0.8,
            spans=tuple(spans),
            sanitised=redact_spans(text, spans),
            detail=f"labels: {labels}",
        )


class SecretGuard(Guard):
    """Blocks credentials on the way out.

    Fails **closed** (``BLOCK``, not redact) by default. A leaked key cannot be un-leaked, and a
    blocked response is recoverable. This is the split ADR-006 describes: exfiltration guards
    fail closed, quality guards fail open.

    Matters concretely here because the legacy design passes a Google OAuth access token in the
    request body (``main.py:207``), so live credentials share a process with the prompt.
    """

    name = "secret"
    stages = (GuardStage.OUTPUT, GuardStage.TOOL_RESULT)

    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}\b"), "api_key"),
        (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "google_api_key"),
        (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "github_token"),
        (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat"),
        (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "slack_token"),
        (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}\b"), "google_oauth_token"),
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private_key"),
        (re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,}\b"), "jwt"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    )

    def __init__(self, *, block: bool = True) -> None:
        self._block = block

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        spans = [
            Span(m.start(), m.end(), label, "")
            for pattern, label in self._PATTERNS
            for m in pattern.finditer(text)
        ]
        if not spans:
            return GuardVerdict.allow(self.name)

        labels = sorted({s.label for s in spans})
        if self._block:
            return GuardVerdict(
                guard=self.name,
                action=GuardAction.BLOCK,
                reason="credential_in_output",
                confidence=0.95,
                spans=tuple(spans),
                detail=f"labels: {labels}",
            )
        return GuardVerdict(
            guard=self.name,
            action=GuardAction.REDACT,
            reason="credential_redacted",
            confidence=0.95,
            spans=tuple(spans),
            sanitised=redact_spans(text, spans),
            detail=f"labels: {labels}",
        )


class SystemPromptLeakGuard(Guard):
    """Detects the system prompt being echoed back.

    Compares distinctive n-grams from the actual system prompt against the output rather than
    looking for the string "system prompt", so it catches a successful extraction regardless of
    how it was phrased. Requires the prompt via ``context["system_prompt"]``.
    """

    name = "system_prompt_leak"
    stages = (GuardStage.OUTPUT,)

    def __init__(self, *, ngram: int = 8, min_matches: int = 2) -> None:
        self._n = ngram
        self._min_matches = min_matches

    @staticmethod
    def _shingles(text: str, n: int) -> set[str]:
        words = text.lower().split()
        if len(words) < n:
            return set()
        return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        system_prompt = context.get("system_prompt") or ""
        if not system_prompt or len(text.split()) < self._n:
            return GuardVerdict.allow(self.name)

        overlap = self._shingles(system_prompt, self._n) & self._shingles(text, self._n)
        if len(overlap) < self._min_matches:
            return GuardVerdict.allow(self.name)

        return GuardVerdict(
            guard=self.name,
            action=GuardAction.BLOCK,
            reason="system_prompt_leaked",
            confidence=min(0.6 + 0.1 * len(overlap), 0.99),
            detail=f"{len(overlap)} matching {self._n}-gram(s)",
        )


class GroundednessGuard(Guard):
    """Advisory hallucination proxy.

    Estimates how much of an answer is supported by retrieved context, using content-word
    overlap per sentence. This is a *proxy*, not a factuality judgement: a correct sentence
    phrased differently from its source scores low, and a fluent fabrication that reuses source
    vocabulary scores high.

    It therefore **never blocks**. It emits ``aura_groundedness_score`` and
    ``aura_ungrounded_claims_total`` (the Phase 10 hallucination-rate requirement) and feeds
    confidence calibration. Treating a noisy proxy as an enforcement signal would produce
    exactly the kind of confidently-wrong behaviour it is meant to measure.
    """

    name = "groundedness"
    stages = (GuardStage.OUTPUT,)

    _STOPWORDS = frozenset(
        """a an the and or but if then than that this these those is are was were be been being
        of in on at to for with from by as it its it's you your i we they he she them his her
        do does did done have has had can could should would will shall may might must not no
        yes there here what when where who whom which how why about into over under again more
        most some any each other such only own same so too very just also""".split()
    )

    def __init__(self, *, min_score: float = 0.4, min_sentence_words: int = 5) -> None:
        self._min_score = min_score
        self._min_words = min_sentence_words

    def _content_words(self, text: str) -> set[str]:
        # Arabic words are folded and light-stemmed so an answer that says "الطلاب" is supported
        # by a source that says "طلاب". Without the Arabic alternative every Arabic answer had no
        # content words at all, so the guard allowed it unexamined.
        words: set[str] = set()
        for word in re.findall(r"[a-z0-9']+|[\u0621-\u064A]+", fold(text.lower())):
            if len(word) <= 2:
                continue
            if is_arabic_token(word):
                if word in ARABIC_STOPWORDS:
                    continue
                word = light_stem(word)
            elif word in self._STOPWORDS:
                continue
            words.add(word)
        return words

    def _evaluate(self, text: str, *, stage: GuardStage, context: dict) -> GuardVerdict:
        sources: Sequence[str] = context.get("citations_text") or ()
        if not sources:
            # No retrieval happened. Groundedness is undefined, not zero -- reporting 0 here
            # would make every un-grounded chit-chat turn look like a hallucination.
            return GuardVerdict.allow(self.name)

        source_words = set()
        for source in sources:
            source_words |= self._content_words(source)
        if not source_words:
            return GuardVerdict.allow(self.name)
        # Word overlap only means something within one script. An Arabic answer drawn from
        # English documents (an Arabic speaker's everyday case) shares no words with them however
        # faithful it is, so each sentence is scored only on the words written in a script the
        # sources also use; one with too few such words is left unscored, as before this guard
        # could read Arabic at all (guard eval case ground-18).
        source_scripts = {is_arabic_token(w) for w in source_words}

        sentences = [s.strip() for s in re.split(r"(?<=[.!?\u061F\u06D4])\s+", text) if s.strip()]
        scored: list[float] = []
        ungrounded = 0
        for sentence in sentences:
            words = {w for w in self._content_words(sentence) if is_arabic_token(w) in source_scripts}
            if len(words) < self._min_words:
                continue
            support = len(words & source_words) / len(words)
            scored.append(support)
            if support < self._min_score:
                ungrounded += 1

        if not scored:
            return GuardVerdict.allow(self.name)

        overall = sum(scored) / len(scored)
        return GuardVerdict(
            guard=self.name,
            action=GuardAction.FLAG if ungrounded else GuardAction.ALLOW,
            reason="low_groundedness" if ungrounded else "ok",
            confidence=round(overall, 4),
            detail=f"{ungrounded}/{len(scored)} sentence(s) below {self._min_score}",
            metadata={"groundedness": round(overall, 4), "ungrounded_sentences": ungrounded},
        )


# --------------------------------------------------------------------------------------
# Default sets
# --------------------------------------------------------------------------------------


def default_input_guards(
    *, max_chars: int = 32_000, injection_threshold: float = 0.6, jailbreak_threshold: float = 0.7
) -> list[Guard]:
    return [
        SizeGuard(max_chars=max_chars),
        EncodingGuard(),
        PromptInjectionGuard(threshold=injection_threshold),
        JailbreakGuard(threshold=jailbreak_threshold),
    ]


def default_context_guards(
    *, max_chars: int = 32_000, injection_threshold: float = 0.6
) -> list[Guard]:
    """Guards for retrieved chunks, memories, and tool results -- where injection actually lives."""
    return [
        SizeGuard(max_chars=max_chars),
        EncodingGuard(),
        PromptInjectionGuard(threshold=injection_threshold),
    ]


def default_output_guards(
    *, redact_pii: bool = True, block_secrets: bool = True, groundedness_min: float = 0.4
) -> list[Guard]:
    return [
        SecretGuard(block=block_secrets),
        SystemPromptLeakGuard(),
        PIIGuard(redact=redact_pii),
        GroundednessGuard(min_score=groundedness_min),
    ]


def guards_for_stage(guards: Iterable[Guard], stage: GuardStage) -> list[Guard]:
    return [g for g in guards if g.applies_to(stage)]
