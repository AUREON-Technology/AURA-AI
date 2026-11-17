"""The documents ingested for the ``arabic_corpus`` evaluation suite.

Twelve original Arabic documents of the kind a user uploads to a personal assistant (a leave
policy, a rental contract, medication instructions, a travel itinerary, and so on), written mostly
in Modern Standard Arabic with Egyptian colloquial phrasing where real documents would use it.
They carry concrete, checkable facts (amounts, dates, durations, names) that tell documents apart,
and overlap on purpose ("إجازة", "اشتراك", "تأمين", shared dates and amounts) so that retrieval is
not a keyword-matching exercise. Hamza, teh-marbuta and alef-maqsura spellings are deliberately
inconsistent, as they are in real text.

Mirrors ``docs_corpus_sources.py``: the mapping (document id -> file) is visible on its own, and
``arabic_corpus.jsonl``'s ``relevant_documents`` names these ids.
"""

from __future__ import annotations

from pathlib import Path

_CORPUS_DIR = Path(__file__).resolve().parent / "arabic_corpus"

# (document_id, title, filename) -- document_id is what arabic_corpus.jsonl's relevant_documents
# names, so it must be stable. Titles are cosmetic only.
ARABIC_CORPUS_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("leave-policy", "سياسة الإجازات للعاملين", "leave-policy.md"),
    ("rental-contract", "عقد إيجار شقة سكنية", "rental-contract.md"),
    ("medication-instructions", "تعليمات العلاج", "medication-instructions.md"),
    ("travel-itinerary", "برنامج رحلة الأقصر وأسوان", "travel-itinerary.md"),
    ("car-insurance", "ملخص وثيقة التأمين الشامل على السيارة", "car-insurance.md"),
    ("course-regulation", "لائحة مقرر هياكل البيانات المتقدمة", "course-regulation.md"),
    ("mobile-plan", "شروط باقة الموبايل", "mobile-plan.md"),
    ("recipes", "وصفات البيت", "recipes.md"),
    ("it-security-policy", "سياسة أمن المعلومات واستخدام الأجهزة", "it-security-policy.md"),
    ("household-budget", "ميزانية البيت الشهرية", "household-budget.md"),
    ("gym-membership", "شروط وأحكام عضوية الجيم", "gym-membership.md"),
    ("meeting-minutes", "محضر اجتماع فريق المنتج", "meeting-minutes.md"),
)


def load_arabic_corpus_sources() -> tuple[tuple[str, str, str], ...]:
    """(document_id, title, content) for every configured source, read from disk.

    Raises if a file has gone missing -- a corpus doc silently dropping out would make every case
    that names it fail with a confusing "not found" rather than a clear ingestion error.
    """
    loaded = []
    for document_id, title, filename in ARABIC_CORPUS_SOURCES:
        path = _CORPUS_DIR / filename
        content = path.read_text(encoding="utf-8")
        loaded.append((document_id, title, content))
    return tuple(loaded)
