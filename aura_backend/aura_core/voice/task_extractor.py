"""Voice (or typed) request to a structured task, without guessing (ADR-041, roadmap row 88).

Spoken instructions are loose: "hey, can you fix the login bug in acme/web and open a PR, no rush,
and don't touch the payments code". A task the agent can run needs the parts named. This turns the
transcript into::

    {goal, constraints[], repository?, deliverable?, priority?, ambiguities[], needs_clarification,
     questions[]}

and -- the rule everything else follows -- **never fills a gap with a guess**. A missing goal, a
code task with no repository, a repository named without its owner, a goal that is only "fix it",
a work request with no stated deliverable: each becomes an *ambiguity* and a *question*, and
``needs_clarification`` is true. The caller (the client) asks, the person answers, and only then
does the client ``POST /agents/tasks``. This module never starts anything.

Two stages, the first always:

1. :func:`extract_task` -- deterministic, English and Arabic cues, no model. What it returns is
   reproducible and is the fallback for everything below.
2. :class:`TaskExtractor` -- an optional refinement by a model through the router, for phrasing the
   cues miss. Strict JSON, repaired once, else discarded in favour of stage 1. The model's output
   is *checked against the transcript* before it is believed: a repository that is not literally in
   what the person said is dropped, and ``needs_clarification`` is always recomputed here, never
   taken from the model.

Stdlib and domain only (ADR-001); the router arrives as a collaborator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..domain.llm import CompletionRequest, SamplingParams
from ..domain.messages import Conversation, Message
from ..nlp.normalize import normalize_transcript
from ..observability.logging import get_logger
from ..text.arabic import dominant_script

__all__ = ["StructuredTask", "TaskExtractor", "extract_task", "task_goal_text"]

logger = get_logger("voice.task_extractor")

PRIORITIES = ("high", "normal", "low")
MAX_GOAL_CHARS = 500
_MAX_ITEMS = 12


# -- helpers ---------------------------------------------------------------------------------


def _ar(phrase: str) -> str:
    """A regex for an Arabic phrase that tolerates the spellings writers use interchangeably
    (hamza-carrying alefs, ya/alef-maqsura, ta-marbuta/ha) and any run of spaces."""
    out: list[str] = []
    for ch in phrase:
        if ch in "اأإآ":
            out.append("[اأإآ]")
        elif ch in "يى":
            out.append("[يى]")
        elif ch in "ةه":
            out.append("[ةه]")
        elif ch == " ":
            out.append(r"\s+")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _alt(*phrases: str, arabic: bool = False) -> str:
    items = sorted(phrases, key=len, reverse=True)
    return "(?:" + "|".join(_ar(p) if arabic else p for p in items) + ")"


# -- lexicon ---------------------------------------------------------------------------------

_FILLER_EN = re.compile(
    r"^(?:(?:hey|hi|hello|ok(?:ay)?|so|um+|uh+|well|right|aura|alright|and|then|also)\b[\s,:-]*)+", re.I)
_REQUEST_EN = re.compile(
    r"^(?:(?:can|could|would|will) you(?: please)?|i(?:'d| would) like (?:you )?to|"
    r"i (?:want|need|would like) you to|i (?:want|need) to|i(?:'d| would) like to|let'?s|"
    r"i want|i need|please|kindly|go ahead and)\b[\s,:-]*", re.I)
_FILLER_AR = re.compile(
    r"^(?:(?:" + _alt("يا", "اورا", "اي", "طيب", "ماشي", "اوكي", "هاي", "مرحبا", "السلام عليكم",
                      arabic=True) + r")\b[\s,،:-]*)+")
_REQUEST_AR = re.compile(
    r"^(?:(?:" + _alt("لو سمحت", "من فضلك", "ممكن", "عايز", "عاوز", "اريد", "احتاج", "محتاج",
                     "نفسي", "بدي", "ابغى", "تقدر", "هل تستطيع", "هل يمكنك", "يمكنك", "اريدك ان",
                     "عايزك", "عاوزك", "ابي", arabic=True) + r")\b[\s,،:-]*)+")

_CONSTRAINT_START = re.compile(
    r"^(?:(?:and|but|also|just|then)\s+)?(?:make sure|be sure|without|don'?t|do not|never|must|"
    r"should|only|avoid|keep|no more than|at most|at least|using|use|before|within|under|limit|"
    r"as long as|preferably|ideally|while|except|by (?:monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|tomorrow|tonight|today|end of|eod|eow|the \d)|not|no)\b", re.I)
_CONSTRAINT_START_AR = re.compile(
    r"^(?:(?:" + _alt("و", "لكن", "بس", "برضو", arabic=True) + r")\s*)?(?:" + _alt(
        "تاكد", "لازم", "يجب", "ينبغي", "بدون", "من غير", "لا تلمس", "ما تلمس", "لا", "ما", "فقط",
        "بس", "قبل", "خلال", "باستخدام", "استخدم", "تجنب", "ركز", "مع مراعاة", "ما عدا", "الا",
        "على الاقل", "على الاكثر", "بحيث", arabic=True) + r")(?=\s|$)")

#: Where a clause may start *inside* a sentence. Narrower than the start-of-sentence list: "use"
#: or "only" in the middle of a request are usually part of its verb phrase, not a new clause.
_STRONG_EN = (r"make sure|be sure|without|don'?t|do not|never|no more than|at most|as long as|ideally|"
              r"preferably|except|while keeping")
#: After a spoken pause (a comma) a broader set of cues starts a new clause.
_WEAK_EN = (r"keep|use|using|only|avoid|make it|it should|it must|it needs to|limit|under|within|before|"
            r"by (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|today|eod|end of)|"
            r"not|no")
_INNER_SPLIT = re.compile(
    r"(?:\s*[,;]\s*(?:(?:and|but|also)\s+)?(?=(?:" + _WEAK_EN + r")\b)|"
    r"(?:\s*[,;]\s*|\s+)(?:(?:and|but|also)\s+)?(?=(?:" + _STRONG_EN + r")\b))", re.I)
_INNER_SPLIT_AR = re.compile(
    r"(?:\s*[،,;]\s*(?:و)?(?=(?:" + _alt("تاكد", "بدون", "من غير", "لازم", "ما تلمس", "لا تلمس", "استخدم",
                                          "فقط", "تجنب", arabic=True) + r")(?=\s|$))|"
    r"\s+(?:(?:" + _alt("و", "لكن", "بس", arabic=True) + r")\s*)?(?=(?:" + _alt(
        "تاكد", "بدون", "من غير", "لازم", arabic=True) + r")(?=\s|$)))")

_LEAD_IN = r"(?:(?:it'?s|it is|this is|that'?s|that is|it can be|and it'?s)\s+)?"
_PRIORITY = (
    # Low first: "no rush" must not be read as the "rush" in a high-priority phrase.
    ("low", re.compile(
        _LEAD_IN + r"\b(?:no rush|not urgent|not that urgent|low[- ]priority|no hurry|can wait|at your leisure|"
        r"(?:whenever|when|if) you (?:have|get) (?:a |the )?(?:time|chance|moment|opportunity)|whenever)\b",
        re.I)),
    ("high", re.compile(
        _LEAD_IN + r"\b(?:urgent(?:ly)?|asap|as soon as possible|immediately|right now|right away|"
        r"high[- ]priority|top priority|critical|an emergency|emergency)\b", re.I)),
    ("normal", re.compile(_LEAD_IN + r"\b(?:normal|regular|standard) priority\b", re.I)),
)
_PRIORITY_AR = (
    ("low", re.compile(_alt("مش مستعجل", "مش ضروري", "على راحتك", "مو مستعجل", "غير عاجل", "ملوش استعجال",
                            "في اي وقت", "عادي ومش مستعجل", arabic=True))),
    ("high", re.compile(_alt("ضروري", "عاجل", "حالا", "الان", "بسرعة", "في اسرع وقت", "فورا", "مستعجل",
                             "اولوية قصوى", arabic=True))),
    ("normal", re.compile(_alt("اولوية عادية", "اولوية متوسطة", arabic=True))),
)

#: Deliverable kinds: canonical name -> English and Arabic surface forms.
_DELIVERABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("pull_request", (r"pull[- ]?request", r"PR", r"merge request", r"MR"), ("بول ريكوست", "طلب دمج")),
    ("patch", (r"patch", r"diff"), ("باتش",)),
    ("tests", (r"(?:unit |integration )?tests?(?: suite)?",), ("اختبارات", "تيستات")),
    ("report", (r"report",), ("تقرير",)),
    ("summary", (r"summary", r"tl;?dr", r"overview", r"digest"), ("ملخص", "خلاصة")),
    ("email", (r"e-?mail", r"reply", r"message"), ("ايميل", "بريد", "رسالة")),
    ("document", (r"document", r"doc", r"write-?up", r"memo", r"readme", r"guide"), ("مستند", "وثيقة", "ملف")),
    ("issue", (r"issue", r"ticket"), ("تذكرة", "ايشو")),
    ("spreadsheet", (r"spreadsheet", r"csv", r"excel", r"sheet"), ("جدول", "اكسل")),
    ("presentation", (r"presentation", r"slides?", r"deck"), ("عرض تقديمي", "سلايدات")),
    ("plan", (r"plan", r"roadmap", r"proposal"), ("خطة", "مقترح")),
    ("list", (r"(?:check)?list",), ("قائمة", "لستة")),
)
_DELIVER_VERB_EN = (
    r"(?:(?:as|into|in)\s+(?:a|an|the)\s+|(?:open|raise|create|submit|send|give|write|draft|prepare|"
    r"generate|produce|make|deliver|output|post|file|add|attach)\s+(?:me\s+|us\s+|up\s+)?"
    r"(?:a|an|the|your|some)?\s*(?:new\s+|short\s+|brief\s+|detailed\s+|quick\s+|draft\s+|full\s+)?)")
_DELIVER_VERB_AR = _alt("اعمل", "اكتب", "ابعت", "ارسل", "جهز", "اطلع", "افتح", "اعطني", "عطني", "ابعتلي",
                        "كمخرج", "في صورة", "على شكل", "ك", arabic=True)

_ANSWER_INTENT = re.compile(
    r"^(?:what|why|how|when|where|who|which|tell me|explain|find out|check (?:if|whether)|"
    r"look up|look into|research|investigate|compare|"
    r"(?:is|are|does|did|do|can|will|was|were)\s+(?:the|there|this|that|my|our|any|we|i|you|a|an)\b)", re.I)
_ANSWER_INTENT_AR = re.compile(
    r"^(?:" + _alt("ما", "ماذا", "لماذا", "ليه", "ازاي", "كيف", "متى", "مين", "من", "اشرح", "قولي", "ابحث",
                   "دور", "قارن", "هل", arabic=True) + r")(?=\s|$)")
_SUMMARIZE = re.compile(r"^(?:summari[sz]e|sum up|recap|tl;?dr)\b", re.I)
_SUMMARIZE_AR = re.compile(r"^(?:" + _alt("لخص", "تلخص", "تلخيص", "يلخص", "نلخص", arabic=True) + r")(?=\s|$)")

_CODE_TASK = re.compile(
    r"\b(?:fix|bug|bugs|implement|refactor|debug|deploy|merge|commit|push|branch|pull[- ]?request|PR|"
    r"repo|repository|codebase|source code|unit tests?|lint|compile|build|CI|pipeline|migration|"
    r"endpoint|function|class|module|stack trace|exception|regression|hotfix|rebase|cherry-?pick)\b", re.I)
_CODE_TASK_AR = re.compile(_alt("باج", "ريبو", "مستودع", "كود", "برمج", "ديبلوي", "كومت", "فرع", "اختبارات",
                                "اصلح", "صلح", "نفذ", "عدل في الكود", "ريفاكتور", "بول ريكوست", arabic=True))

_REPO_SLUG = re.compile(
    r"(?<![\w./@-])([A-Za-z0-9][A-Za-z0-9_.-]{0,38})/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})(?![\w/])")
_REPO_URL = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab|bitbucket)\.(?:com|org)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?=[/\s?#.,;:!)]|$)",
    re.I)
_NOT_A_REPO = frozenset({"and/or", "w/o", "n/a", "24/7", "he/she", "his/her", "yes/no", "on/off", "i/o",
                         "input/output", "read/write", "client/server", "true/false", "either/or"})
_REPO_NAMED_EN = re.compile(
    r"\b(?:in|on|for|to|from|inside|within|of)\s+(?:the\s+|my\s+|our\s+)?([A-Za-z][\w.-]{1,60})\s+(?:repo|repository)\b|"
    r"\b(?:repo|repository)\s+(?:called|named)?\s*([A-Za-z][\w.-]{1,60})\b", re.I)
_REPO_NAMED_AR = re.compile(r"(?:" + _alt("ريبو", "مستودع", "ريبوزيتوري", arabic=True) + r")\s+([A-Za-z][\w.-]{1,60})")
_REPO_UNNAMED = re.compile(
    r"\b(?:the|my|our|this|that|a)\s+(?:repo|repository|codebase|project)\b(?!\s+(?:called|named))", re.I)
_REPO_UNNAMED_AR = re.compile(_alt("الريبو", "المستودع", "المشروع", "الكود", arabic=True))
_STOP_REPO_WORDS = frozenset({"the", "this", "that", "my", "our", "a", "an", "main", "git", "same", "new"})

_VAGUE_OBJECT = re.compile(
    r"\b(?:it|this|that|them|those|these|the thing|that thing|the issue|the bug|the problem|the file|"
    r"the project|the error|the stuff)\b", re.I)
_VAGUE_OBJECT_AR = re.compile(_alt("ده", "دي", "دا", "هذا", "هذه", "ذلك", "الحاجة دي", "الموضوع ده",
                                   "المشكلة", "الباج", arabic=True))
_ALTERNATIVES = re.compile(r"\beither\b.+\bor\b|\bor maybe\b|\bwhichever\b", re.I)

_QUESTIONS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "goal.missing": "What would you like me to do?",
        "goal.vague": "That is too short for me to act on. What exactly should I do, and to what?",
        "goal.reference": "What does “{text}” refer to?",
        "repository.missing": "Which repository should I work in? Give it as owner/name.",
        "repository.partial": "Which owner does the “{text}” repository belong to? Give it as owner/name.",
        "repository.unspecified": "Which repository do you mean? Give it as owner/name.",
        "repository.multiple": "You named more than one repository ({text}). Which one should I work in?",
        "deliverable.missing": "What should I hand back: a pull request, a summary, a report, or something else?",
        "scope.alternatives": "You gave alternatives. Which one should I go with?",
    },
    "ar": {
        "goal.missing": "ايه اللي عايزني أعمله؟",
        "goal.vague": "الطلب قصير جدا ومش قادر أنفذه. بالظبط عايزني أعمل ايه وعلى ايه؟",
        "goal.reference": "تقصد ايه بـ «{text}»؟",
        "repository.missing": "في أنهي مستودع أشتغل؟ اكتبه بالشكل owner/name.",
        "repository.partial": "المستودع «{text}» تابع لمين (المالك)؟ اكتبه بالشكل owner/name.",
        "repository.unspecified": "تقصد أنهي مستودع؟ اكتبه بالشكل owner/name.",
        "repository.multiple": "ذكرت أكتر من مستودع ({text}). أشتغل في أنهي واحد؟",
        "deliverable.missing": "عايز أسلّمك ايه: pull request، ولا ملخص، ولا تقرير، ولا حاجة تانية؟",
        "scope.alternatives": "ذكرت أكتر من اختيار. أمشي على أنهي واحد؟",
    },
}


# -- the result ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructuredTask:
    goal: str
    constraints: tuple[str, ...] = ()
    repository: str | None = None
    deliverable: str | None = None
    deliverable_text: str | None = None
    priority: str | None = None
    ambiguities: tuple[Mapping[str, Any], ...] = ()
    questions: tuple[Mapping[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    language: str = "en"
    source: str = "deterministic"
    #: Repository name heard without an owner; shown to the person, never used as the repository.
    repository_hint: str | None = None
    code_task: bool = False

    @property
    def needs_clarification(self) -> bool:
        return any(a.get("blocking", True) for a in self.ambiguities)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "repository": self.repository,
            "deliverable": self.deliverable,
            "deliverable_text": self.deliverable_text,
            "priority": self.priority,
            "ambiguities": [dict(a) for a in self.ambiguities],
            "needs_clarification": self.needs_clarification,
            "questions": [dict(q) for q in self.questions],
            "assumptions": list(self.assumptions),
            "repository_hint": self.repository_hint,
            "code_task": self.code_task,
            "language": self.language,
            "source": self.source,
        }


def task_goal_text(task: StructuredTask) -> str:
    """The goal as one block of text suitable for ``POST /agents/tasks``'s ``goal``: the goal, then
    each constraint, repository and deliverable on its own line, so nothing the person said is
    lost between the transcript and the agent."""
    lines = [task.goal.rstrip(".") + "."]
    if task.repository:
        lines.append(f"Repository: {task.repository}")
    if task.deliverable:
        lines.append(f"Deliverable: {task.deliverable_text or task.deliverable}")
    lines.extend(f"Constraint: {c}" for c in task.constraints)
    return "\n".join(lines)


# -- deterministic extraction ----------------------------------------------------------------


def _strip_leads(text: str, language: str) -> str:
    previous = None
    out = text.strip()
    for _ in range(5):
        if out == previous:
            break
        previous = out
        if language == "ar":
            out = _FILLER_AR.sub("", out).strip()
            out = _REQUEST_AR.sub("", out).strip()
        out = _FILLER_EN.sub("", out).strip()
        out = _REQUEST_EN.sub("", out).strip()
    return out.strip(" ,.;:-،؛")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?؟])\s+|\n+|;", text) if s and s.strip()]


def _clauses(sentence: str, language: str) -> list[str]:
    splitter = _INNER_SPLIT_AR if language == "ar" else _INNER_SPLIT
    parts = splitter.split(sentence)
    if language == "ar":
        parts = [q for part in parts for q in _INNER_SPLIT.split(part)]
    return [p.strip(" ,.;:-،؛") for p in parts if p and p.strip(" ,.;:-،؛")]


def _is_constraint(clause: str, language: str) -> bool:
    if _CONSTRAINT_START.match(clause):
        return True
    return language == "ar" and bool(_CONSTRAINT_START_AR.match(clause))


def _find_priority(text: str) -> tuple[str | None, str]:
    for label, pattern in _PRIORITY_AR:
        found = pattern.search(text)
        if found:
            return label, text[: found.start()] + " " + text[found.end():]
    for label, pattern in _PRIORITY:
        found = pattern.search(text)
        if found:
            return label, text[: found.start()] + " " + text[found.end():]
    return None, text


def _tidy(text: str) -> str:
    """Collapse the gaps and doubled commas left where a priority phrase was cut out."""
    out = re.sub(r"\s+", " ", text)
    out = re.sub(r"\s*([,،;])(?:\s*[,،;])+", r"\1", out)
    out = re.sub(r"\s+([,،;.!?؟])", r"\1", out)
    return re.sub(r"(?:\s*[,،;])+\s*$", "", out).strip()


def _find_repositories(text: str) -> list[str]:
    found: list[str] = []
    for match in _REPO_URL.finditer(text):
        found.append(f"{match.group(1)}/{match.group(2)}")
    for match in _REPO_SLUG.finditer(text):
        owner, name = match.group(1), match.group(2)
        slug = f"{owner}/{name}"
        if slug.lower() in _NOT_A_REPO or len(owner) < 2 or len(name) < 2:
            continue
        if not re.search(r"[A-Za-z]", owner) or owner.isdigit() or name.isdigit():
            continue
        if re.fullmatch(r"[\d.-]+", name):
            continue
        # A path inside something already counted as a URL is not a second repository.
        if any(slug.lower() in f.lower() or f.lower() in slug.lower() for f in found):
            continue
        found.append(slug)
    seen: dict[str, None] = {}
    for slug in found:
        seen.setdefault(slug, None)
    return list(seen)


def _find_deliverable(text: str) -> tuple[str | None, str | None]:
    best: tuple[int, str, str] | None = None
    for kind, english, arabic in _DELIVERABLES:
        for form in english:
            found = re.search(_DELIVER_VERB_EN + "(" + form + r")\b", text, re.I)
            if found and (best is None or found.start() < best[0]):
                best = (found.start(), kind, found.group(1))
        for form in arabic:
            found = re.search(r"(?:" + _DELIVER_VERB_AR + r")\s*(?:" + _ar("ل") + r")?\s*(" + _ar(form) + r")", text)
            if found and (best is None or found.start() < best[0]):
                best = (found.start(), kind, found.group(1))
    if best is None:
        # A bare mention of the deliverable word as the thing wanted: "I want a report".
        for kind, english, _ar_forms in _DELIVERABLES:
            for form in english:
                found = re.search(r"\b(?:want|need|expect|wanted|deliverable is)\s+(?:a|an|the|some)?\s*(" + form + r")\b",
                                  text, re.I)
                if found and (best is None or found.start() < best[0]):
                    best = (found.start(), kind, found.group(1))
    return (best[1], best[2]) if best else (None, None)


def _kind_of(phrase: str) -> str | None:
    """The canonical deliverable kind a phrase such as "a short report" names, if any."""
    for kind, english, arabic in _DELIVERABLES:
        if any(re.search(r"\b" + form + r"\b", phrase, re.I) for form in english):
            return kind
        if any(re.search(_ar(form), phrase) for form in arabic):
            return kind
    return None


def _leading_deliverable(goal: str, language: str) -> tuple[str | None, str | None]:
    """A request that *is* its deliverable: "a report on last month's sales", "تقرير عن ...". Only
    the opening words count -- a report mentioned later in the sentence is a subject, not a want."""
    for kind, english, arabic in _DELIVERABLES:
        for form in english:
            found = re.match(r"(?:(?:a|an|the|some|your)\s+)?(?:short\s+|brief\s+|quick\s+|detailed\s+|full\s+)?("
                             + form + r")\b(?=\s+(?:on|about|of|for|covering|from)\b)", goal, re.I)
            if found:
                return kind, found.group(1)
        if language == "ar":
            for form in arabic:
                found = re.match(r"(" + _ar(form) + r")(?=\s|$)", goal)
                if found:
                    return kind, found.group(1)
    return None, None


def extract_task(text: str, *, language: str | None = None) -> StructuredTask:
    """The deterministic extraction of ``text``. Pure; same input, same output."""
    raw = normalize_transcript(text or "")
    lang = language if language in ("ar", "en") else (dominant_script(raw) or "en")
    q = _QUESTIONS[lang]
    ambiguities: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    assumptions: list[str] = []

    def flag(field_: str, reason: str, key: str, *, text_: str = "", options: list[str] | None = None) -> None:
        ambiguities.append({"field": field_, "reason": reason, "text": text_ or None, "blocking": True})
        question: dict[str, Any] = {"field": field_, "question": q[key].format(text=text_)}
        if options:
            question["options"] = options
        questions.append(question)

    if not raw.strip():
        flag("goal", "missing", "goal.missing")
        return StructuredTask(goal="", ambiguities=tuple(ambiguities), questions=tuple(questions),
                              language=lang)

    priority, without_priority = _find_priority(raw)
    repos = _find_repositories(raw)

    constraints: list[str] = []
    goal_parts: list[str] = []
    for sentence in _sentences(_tidy(without_priority)):
        for clause in _clauses(sentence, lang):
            cleaned = _strip_leads(clause, lang)
            if not cleaned:
                continue
            if goal_parts and _is_constraint(cleaned, lang):
                constraints.append(cleaned)
            else:
                # The first clause is the goal; a later one that is not a constraint is more of it
                # ("... and then update the docs").
                goal_parts.append(cleaned)
    goal = ". ".join(goal_parts)[:MAX_GOAL_CHARS].strip()

    deliverable, deliverable_text = _find_deliverable(raw)

    code_task = bool(_CODE_TASK.search(raw) or (lang == "ar" and _CODE_TASK_AR.search(raw)) or repos)
    # --- goal ---
    words = [w for w in re.split(r"\s+", goal) if w]
    if not goal:
        flag("goal", "missing", "goal.missing")
    else:
        vague = _VAGUE_OBJECT.search(goal) or (lang == "ar" and _VAGUE_OBJECT_AR.search(goal))
        if len(words) <= 2:
            flag("goal", "vague", "goal.vague", text_=goal)
        elif vague and len(words) <= 5 and not repos and not re.search(r"[\"'“”‘’`]", goal):
            flag("goal", "reference", "goal.reference", text_=vague.group(0))
    if _ALTERNATIVES.search(goal):
        flag("scope", "alternatives", "scope.alternatives")

    # --- repository ---
    repository: str | None = None
    hint: str | None = None
    if len(repos) == 1:
        repository = repos[0]
    elif len(repos) > 1:
        flag("repository", "multiple", "repository.multiple", text_=", ".join(repos), options=repos)
    elif code_task:
        named = _REPO_NAMED_EN.search(raw) or _REPO_NAMED_AR.search(raw)
        if named:
            candidate = next((g for g in named.groups() if g), "")
            if candidate and candidate.lower() not in _STOP_REPO_WORDS:
                hint = candidate
                flag("repository", "partial", "repository.partial", text_=candidate)
        if hint is None:
            if _REPO_UNNAMED.search(raw) or (lang == "ar" and _REPO_UNNAMED_AR.search(raw)):
                flag("repository", "unspecified", "repository.unspecified")
            else:
                flag("repository", "missing", "repository.missing")

    # --- deliverable ---
    if deliverable is None and goal:
        deliverable, deliverable_text = _leading_deliverable(goal, lang)
    if deliverable is None and goal:
        if _SUMMARIZE.match(goal) or (lang == "ar" and _SUMMARIZE_AR.match(goal)):
            deliverable, deliverable_text = "summary", "a summary"
            assumptions.append("deliverable: a summary, because the request is to summarise")
        elif _ANSWER_INTENT.match(goal) or (lang == "ar" and _ANSWER_INTENT_AR.match(goal)):
            deliverable, deliverable_text = "answer", "an answer"
            assumptions.append("deliverable: an answer, because the request is a question")
        else:
            flag("deliverable", "missing", "deliverable.missing",
                 options=["pull_request", "summary", "report", "document", "answer"] if code_task else None)

    return StructuredTask(
        goal=goal, constraints=tuple(constraints[:_MAX_ITEMS]), repository=repository,
        deliverable=deliverable, deliverable_text=deliverable_text, priority=priority,
        ambiguities=tuple(ambiguities), questions=tuple(questions), assumptions=tuple(assumptions),
        language=lang, repository_hint=hint, code_task=code_task,
    )


# -- model refinement ------------------------------------------------------------------------

_SYSTEM = (
    "You turn a spoken or typed request into a task description. Reply with ONE JSON object and "
    "nothing else, with exactly these keys: "
    '"goal" (string, the main thing to do, in the speaker\'s language, at most 400 characters), '
    '"constraints" (array of strings: limits or conditions the speaker stated), '
    '"repository" (string "owner/name" ONLY if the speaker said it, else null), '
    '"deliverable" (string: what should be handed back, if the speaker said it, else null), '
    '"priority" ("high", "normal", "low", or null if not stated), '
    '"ambiguities" (array of {"field": one of goal|repository|deliverable|scope, "reason": string} '
    "for anything unclear or missing). Never invent a repository, a deliverable or a constraint the "
    "speaker did not state; use null and list an ambiguity instead."
)


def _loads_object(text: str) -> dict[str, Any] | None:
    body = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.S)
    if fenced:
        body = fenced.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _check(value: dict[str, Any]) -> str | None:
    """Why ``value`` is not an acceptable refinement, or ``None`` when it is."""
    goal = value.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return "goal must be a non-empty string"
    if len(goal) > 1000:
        return "goal is too long"
    constraints = value.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        return "constraints must be an array of strings"
    for key in ("repository", "deliverable"):
        if value.get(key) is not None and not isinstance(value.get(key), str):
            return f"{key} must be a string or null"
    if value.get("priority") not in (None, *PRIORITIES):
        return "priority must be high, normal, low or null"
    ambiguities = value.get("ambiguities", [])
    if not isinstance(ambiguities, list) or not all(
        isinstance(a, dict) and isinstance(a.get("field"), str) and isinstance(a.get("reason"), str)
        for a in ambiguities
    ):
        return "ambiguities must be an array of {field, reason}"
    return None


@dataclass(slots=True)
class Refinement:
    status: str
    task: StructuredTask | None = None
    detail: str | None = None


class TaskExtractor:
    """Deterministic extraction, optionally refined by a model through ``router``."""

    def __init__(self, router: Any = None, *, timeout_s: float = 60.0, max_output_tokens: int = 320) -> None:
        self._router = router
        self._timeout_s = timeout_s
        self._max_tokens = max_output_tokens

    async def extract(self, text: str, *, language: str | None = None, refine: bool = True
                      ) -> tuple[StructuredTask, dict[str, Any]]:
        base = extract_task(text, language=language)
        if not refine:
            return base, {"requested": False, "status": "not_requested"}
        if self._router is None or not base.goal:
            return base, {"requested": True, "status": "unavailable" if self._router is None else "skipped"}
        outcome = await self._refine(text, base)
        info: dict[str, Any] = {"requested": True, "status": outcome.status}
        if outcome.detail:
            info["detail"] = outcome.detail
        return (outcome.task or base), info

    async def _refine(self, text: str, base: StructuredTask) -> Refinement:
        conversation = [Message.system(_SYSTEM), Message.user(text.strip()[:4000])]
        output = ""
        problem = "the reply was not a JSON object"
        for attempt in range(2):
            try:
                response = await self._router.complete(CompletionRequest(
                    conversation=Conversation.of(conversation),
                    sampling=SamplingParams(temperature=0.0, max_output_tokens=self._max_tokens),
                    timeout_s=self._timeout_s,
                ))
            except Exception as exc:
                # No eligible provider (a transcript is personal data, so only a local model may
                # see it), a timeout, an outage: the deterministic result stands.
                logger.info("task refinement unavailable", extra={"error": type(exc).__name__})
                return Refinement("unavailable", detail=type(exc).__name__)
            output = response.text or ""
            value = _loads_object(output)
            if value is not None:
                problem_or_none = _check(value)
                if problem_or_none is None:
                    return Refinement("applied", task=self._merge(text, base, value))
                problem = problem_or_none
            else:
                problem = "the reply was not a JSON object"
            if attempt == 0:
                conversation = [
                    *conversation, Message.assistant(output[:2000]),
                    Message.user(f"That was not valid: {problem}. Reply again with ONLY the JSON object."),
                ]
        return Refinement("invalid_output", detail=problem)

    def _merge(self, text: str, base: StructuredTask, value: Mapping[str, Any]) -> StructuredTask:
        """Believe the model only where the transcript backs it, and re-derive the clarification
        state from the merged fields -- the model's own opinion of "is this clear" is not used."""
        lowered = normalize_transcript(text).lower()
        language = base.language
        q = _QUESTIONS[language]

        goal = " ".join(str(value["goal"]).split())[:MAX_GOAL_CHARS]
        constraints = [" ".join(c.split()) for c in value.get("constraints", []) if c.strip()][:_MAX_ITEMS]
        # A constraint the speaker never voiced is an invention; keep only ones whose words occur.
        constraints = [c for c in constraints if _mostly_in(c, lowered)] or list(base.constraints)

        repository = value.get("repository")
        repo_candidates = _find_repositories(text)
        if isinstance(repository, str) and repository.strip():
            repository = repository.strip()
            if repository.lower() not in {r.lower() for r in repo_candidates}:
                repository = None
        else:
            repository = None
        repository = repository or base.repository

        deliverable = value.get("deliverable")
        deliverable_text = None
        if isinstance(deliverable, str) and deliverable.strip():
            if _mostly_in(deliverable, lowered):
                deliverable_text = deliverable.strip()
                deliverable = _kind_of(deliverable_text) or "other"
            else:
                deliverable = None
        else:
            deliverable = None
        if deliverable is None:
            deliverable, deliverable_text = base.deliverable, base.deliverable_text

        priority = value.get("priority") or base.priority

        # Rebuild the gaps from the *merged* fields, using the deterministic rules.
        ambiguities: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []

        def flag(field_: str, reason: str, key: str, text_: str = "", options: list[str] | None = None) -> None:
            ambiguities.append({"field": field_, "reason": reason, "text": text_ or None, "blocking": True})
            question: dict[str, Any] = {"field": field_, "question": q[key].format(text=text_)}
            if options:
                question["options"] = options
            questions.append(question)

        words = goal.split()
        if len(words) <= 2:
            flag("goal", "vague", "goal.vague", goal)
        carried = {(a.get("field"), a.get("reason")) for a in base.ambiguities}
        if ("goal", "reference") in carried:
            ref = next((a for a in base.ambiguities if a.get("reason") == "reference"), {})
            if goal.split() == words and len(words) <= 5:
                flag("goal", "reference", "goal.reference", ref.get("text") or "")
        if ("scope", "alternatives") in carried:
            flag("scope", "alternatives", "scope.alternatives")
        if repository is None and base.code_task:
            for a in base.ambiguities:
                if a.get("field") == "repository":
                    flag("repository", a["reason"], f"repository.{a['reason']}", a.get("text") or "",
                         options=_find_repositories(text) or None)
                    break
        if deliverable is None:
            flag("deliverable", "missing", "deliverable.missing")
        return StructuredTask(
            goal=goal, constraints=tuple(constraints), repository=repository, deliverable=deliverable,
            deliverable_text=deliverable_text, priority=priority, ambiguities=tuple(ambiguities),
            questions=tuple(questions), assumptions=base.assumptions, language=language, source="llm",
            repository_hint=base.repository_hint, code_task=base.code_task,
        )


def _mostly_in(candidate: str, lowered_transcript: str) -> bool:
    """Whether most of ``candidate``'s words occur in the transcript -- the check that a model's
    constraint or deliverable was something the person said rather than something it made up."""
    tokens = [t for t in re.findall(r"[\w.-]+", candidate.lower()) if len(t) > 2]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in lowered_transcript)
    return hits / len(tokens) >= 0.6
