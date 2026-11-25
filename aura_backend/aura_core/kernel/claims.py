"""Answers that claim an action no tool performed (audit A1).

The rule "never claim you ran a tool" lived only in the system prompt (``_CORE_IDENTITY``) and,
when planning raised, in ``PLANNING_FAILED_NOTE``. When planning was simply *skipped* -- the
common case, since the planner gate is a heuristic -- nothing told the model, and nothing in code
noticed an answer of "Done, I added the task" with no action behind it. A person then believes a
reminder exists that does not.

This is the code half of that rule. After generation the kernel asks
:func:`claims_completed_action`; if the answer claims a completed action and the turn has nothing
that could back it (no tool ran successfully, nothing awaits confirmation), the kernel appends
:func:`unbacked_action_note`, sends the corrected text as a ``revision`` frame on the stream,
persists the corrected text, and marks the turn degraded with ``unbacked_action_claim``.

Precision over recall, deliberately. A miss leaves the turn exactly as it was before this module;
a false hit appends a note that is still true (no action was taken this turn), so its cost is a
redundant sentence, never a wrong one.
"""

from __future__ import annotations

import re

from ..domain.text import fold_arabic

__all__ = ["claims_completed_action", "unbacked_action_note"]

#: Past-tense verbs a reply uses to report having done something to the user's data. Not
#: "noted"/"remembered": background memory extraction can legitimately back those.
_EN_VERBS = (
    r"added|created|scheduled|booked|sent|emailed|saved|stored|recorded|logged|set(?: up)?|"
    r"deleted|removed|cancel+ed|updated|renamed|uploaded|submitted|registered|reserved|"
    r"committed|pushed"
)

_EN_PATTERNS = (
    # "I've added ...", "I have scheduled ...", "I just created ...", "I added ..."
    re.compile(
        rf"\bI(?:'ve| have)?\s+(?:just\s+|now\s+|already\s+|successfully\s+)?(?:{_EN_VERBS})\b",
        re.I,
    ),
    # "... has been added", "... was successfully scheduled", "... is now saved"
    re.compile(
        rf"\b(?:has|have)\s+(?:now\s+)?been\s+(?:successfully\s+)?(?:{_EN_VERBS})\b"
        rf"|\bwas\s+successfully\s+(?:{_EN_VERBS})\b"
        rf"|\b(?:is|are)\s+now\s+(?:{_EN_VERBS})\b",
        re.I,
    ),
    # A reply that opens with "Done." / "All set!" / "Done -- ..."
    re.compile(r"^\W*(?:done|all set|all done)\b", re.I),
)

#: Arabic, matched on folded text (no diacritics, bare alefs, final ة as ه): "تمت إضافة",
#: "تم حفظ", "أضفت", "قمت بإضافة", "لقد سجلت", a reply opening with "تم".
_AR_ACTION_NOUNS = r"اضاف|انشاء|حفظ|تسجيل|جدول|حجز|ارسال|تحديث|تعديل|حذف|الغاء|ضبط|تعيين|اعداد|رفع"
_AR_PATTERNS = (
    re.compile(rf"(?:^|\s)تمت?\s+(?:بنجاح\s+)?(?:ال)?(?:{_AR_ACTION_NOUNS})"),
    re.compile(rf"(?:^|\s)قمت\s+ب(?:ال)?(?:{_AR_ACTION_NOUNS})"),
    re.compile(
        r"(?:^|\s)(?:لقد\s+|قد\s+)?(?:اضفت|انشات|حفظت|سجلت|جدولت|حجزت|ارسلت|حدثت|حذفت|"
        r"الغيت|ضبطت)(?:ها|ه|هم)?(?=\s|$|[.,!،])"
    ),
    re.compile(r"^\W*تم(?=\s|$|[.!،])"),
)

#: A claim negated or deferred in the same sentence is not a claim: "I haven't added it",
#: "I couldn't save it", "once you confirm I'll have it added", "لم أتمكن من الحفظ".
_NEGATION = re.compile(
    r"n't\b|\b(?:not|never|cannot|unable|failed to|shall i|should i|would you like|do you want|"
    r"once you|after you|if you|before i)\b"
    r"|(?:^|\s)(?:لم|لن|لا|تعذر|هل|بعد\s+موافقتك|عند\s+موافقتك)(?=\s|$)",
    re.I,
)

_SENTENCE = re.compile(r"[^.!?\n؟]+[.!?؟]?")


def claims_completed_action(answer: str) -> bool:
    """Whether ``answer`` reports, as done, an action on the user's data or the world.

    An answer with a fenced code block is not checked: there "I've updated your function" is
    about the code shown, not a claim that something ran. Precision over recall, as above.
    """
    if not answer or not answer.strip() or "```" in answer:
        return False
    for sentence in _SENTENCE.findall(answer):
        stripped = sentence.strip()
        if not stripped or stripped.endswith(("?", "؟")):
            continue
        folded = fold_arabic(stripped)
        hit = any(p.search(stripped) for p in _EN_PATTERNS) or any(
            p.search(folded) for p in _AR_PATTERNS
        )
        if hit and not _NEGATION.search(folded):
            return True
    return False


_NOTE = {
    "en": (
        "Correction: no action was actually taken in this turn. Nothing was created, changed, "
        "sent or scheduled. Ask again if you want me to do it."
    ),
    "ar": (
        "تصحيح: لم يُنفَّذ أي إجراء فعليًا في هذه الرسالة، فلم يُنشأ أو يُعدَّل أو يُرسَل أو "
        "يُجدوَل شيء. اطلب ذلك مرة أخرى إن أردت تنفيذه."
    ),
}


def unbacked_action_note(language: str | None) -> str:
    """The fixed correction appended to an answer whose action claim nothing backs."""
    return _NOTE["ar" if language == "ar" else "en"]
