"""The documents ingested for the ``docs_corpus`` evaluation suite.

Phase A3 needs a real corpus, not synthetic fixture text. This ingests the repository's own
architecture ADRs -- real prose with real, checkable facts and clean per-document boundaries,
which is what makes ``relevant_documents`` in ``docs_corpus.jsonl`` a meaningful assertion rather
than a guess.

Kept as a Python module rather than inlined into the test so the mapping (document id -> path) is
visible on its own and can be extended without touching the test or the case file.
"""

from __future__ import annotations

from pathlib import Path

# Repo root, walking up from aura_backend/evaluation/suites/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ADR_DIR = _REPO_ROOT / "docs" / "architecture"

# (document_id, title, filename) -- document_id is what docs_corpus.jsonl's relevant_documents
# names, so it must be stable. Titles are cosmetic only.
DOCS_CORPUS_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("overview", "AURA Architecture Overview", "00-overview.md"),
    ("adr-001-hexagonal-core", "ADR-001 Hexagonal Core", "001-hexagonal-core.md"),
    ("adr-002-strangler-migration", "ADR-002 Strangler Migration", "002-strangler-migration.md"),
    ("adr-003-provider-routing", "ADR-003 Provider Routing", "003-provider-routing.md"),
    ("adr-004-memory-taxonomy", "ADR-004 Memory Taxonomy", "004-memory-taxonomy.md"),
    ("adr-005-hybrid-retrieval", "ADR-005 Hybrid Retrieval", "005-hybrid-retrieval.md"),
    ("adr-006-safety-pipeline", "ADR-006 Safety Pipeline", "006-safety-pipeline.md"),
    ("adr-007-versioned-indexes", "ADR-007 Versioned Indexes", "007-versioned-indexes.md"),
    (
        "adr-008-human-in-the-loop-learning",
        "ADR-008 Human-in-the-Loop Learning",
        "008-human-in-the-loop-learning.md",
    ),
    ("adr-009-observability-first", "ADR-009 Observability First", "009-observability-first.md"),
)


def load_docs_corpus_sources() -> tuple[tuple[str, str, str], ...]:
    """(document_id, title, content) for every configured source, read from disk.

    Raises if a file has gone missing -- a corpus doc silently dropping out would make every case
    that names it fail with a confusing "not found" rather than a clear ingestion error.
    """
    loaded = []
    for document_id, title, filename in DOCS_CORPUS_SOURCES:
        path = _ADR_DIR / filename
        content = path.read_text(encoding="utf-8")
        loaded.append((document_id, title, content))
    return tuple(loaded)
