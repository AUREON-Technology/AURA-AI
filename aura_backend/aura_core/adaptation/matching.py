"""Cross-source entity merge proposals (AP4).

Two connectors often describe one concept: the ``employees`` table and the API's ``Employee``
schema. This module *proposes* that they are the same thing; it never merges them. A proposal is
a RELATION element with ``relation_type=same_as`` and ``status=proposed`` whose confidence is
its score, so it is versioned, diffed and evidence-linked like everything else in the twin, and
a Super Admin can accept or reject it later (AP9). Nothing auto-applies (ADR-024).

The score is deterministic and explainable:

* **name** (weight 0.5): the entity names as singular word tokens -- 1.0 when equal, otherwise
  their Jaccard overlap;
* **shape** (weight 0.5): the Jaccard overlap of attribute names (casing-insensitive), discounted
  by how many shared attributes disagree on their coarse semantic type -- the profile-similarity
  part: ``salary`` as a measure on one side and as text on the other counts against the match.

Only a *mutual* best match per pair of sources above :data:`MERGE_THRESHOLD` is proposed, so one
entity never ends up proposed as the same as two others in the same source.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.ausm import AusmElement, ElementKind, Provenance
from ..domain.untrusted import UntrustedText
from .normalize import name_tokens, singular

__all__ = ["merge_proposals", "MERGE_THRESHOLD", "MATCHER_EXTRACTOR"]

MATCHER_EXTRACTOR = "entity_matcher"
MERGE_THRESHOLD = 0.5
#: A proposal is a guess about another system's meaning; it never reaches certainty.
_MAX_CONFIDENCE = 0.95
_MAX_LOCATOR = 512


def _identifier(element: AusmElement) -> str:
    value = element.properties.get("identifier")
    return value if isinstance(value, str) else element.locator


def _name_key(element: AusmElement) -> tuple[str, ...]:
    return tuple(singular(t) for t in name_tokens(_identifier(element)))


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _name_score(a: AusmElement, b: AusmElement) -> float:
    left, right = _name_key(a), _name_key(b)
    if not left or not right:
        return 0.0
    return 1.0 if left == right else _jaccard(set(left), set(right))


def _compatible(a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    loose = {"category", "status", "text", "email", "url", "uuid"}
    return a in loose and b in loose


def merge_proposals(elements: Sequence[AusmElement]) -> list[AusmElement]:
    """``same_as`` RELATION proposals between entities of different sources."""
    entities: dict[str, list[AusmElement]] = {}
    attributes: dict[str, dict[str, str | None]] = {}
    for element in elements:
        if element.kind is ElementKind.ENTITY:
            entities.setdefault(element.source_id, []).append(element)
        elif element.kind is ElementKind.ATTRIBUTE and element.parent:
            key = "_".join(name_tokens(_identifier(element)))
            attributes.setdefault(element.parent, {})[key] = element.properties.get("semantic_type")

    def score(a: AusmElement, b: AusmElement) -> tuple[float, float, float, int]:
        name = _name_score(a, b)
        left, right = attributes.get(a.id, {}), attributes.get(b.id, {})
        shared = set(left) & set(right)
        overlap = _jaccard(set(left), set(right))
        agree = sum(1 for k in shared if _compatible(left[k], right[k]))
        shape = overlap * (0.5 + 0.5 * agree / len(shared)) if shared else 0.0
        return round(0.5 * name + 0.5 * shape, 4), round(name, 4), round(shape, 4), len(shared)

    out: list[AusmElement] = []
    sources = sorted(entities)
    for i, left_source in enumerate(sources):
        for right_source in sources[i + 1:]:
            lefts, rights = entities[left_source], entities[right_source]
            scored = {(a.id, b.id): score(a, b) for a in lefts for b in rights}

            # Deterministic: highest score, ties to the smallest id.
            best_right = {a.id: min(rights, key=lambda b: (-scored[(a.id, b.id)][0], b.id)) for a in lefts}
            best_left = {b.id: min(lefts, key=lambda a: (-scored[(a.id, b.id)][0], a.id)) for b in rights}
            for a in lefts:
                b = best_right.get(a.id)
                if b is None or best_left[b.id].id != a.id:
                    continue
                total, name, shape, shared = scored[(a.id, b.id)]
                if total < MERGE_THRESHOLD:
                    continue
                locator = f"same_as:{b.source_id}:{b.locator}"
                if len(locator) > _MAX_LOCATOR:
                    continue
                confidence = min(total, _MAX_CONFIDENCE)
                out.append(AusmElement(
                    a.source_id, locator, ElementKind.RELATION,
                    UntrustedText.of(f"{a.label.raw()} ≈ {b.label.raw()}", max_chars=200),
                    (
                        Provenance(a.source_id, a.locator, MATCHER_EXTRACTOR, confidence, a.provenance[0].evidence_hash),
                        Provenance(b.source_id, b.locator, MATCHER_EXTRACTOR, confidence, b.provenance[0].evidence_hash),
                    ),
                    refs={"from": a.id, "to": b.id},
                    properties={
                        "relation_type": "same_as", "status": "proposed", "score": total,
                        "name_score": name, "shape_score": shape, "shared_attributes": shared,
                    },
                ))
    return out
