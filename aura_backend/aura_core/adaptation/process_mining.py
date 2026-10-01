"""Process mining v0: workflow and state-transition candidates (AP4).

A status attribute with a closed value set is the skeleton of a process. This module proposes
that process as WORKFLOW elements -- one ``workflow:`` element per status attribute, and one
``transition:`` element per candidate transition -- each confidence-scored and citing the
evidence it rests on. Everything here is a *candidate* (confidence < 1); nothing is asserted.

Two inputs, both deterministic:

**Structure** (:func:`workflow_candidates`), from the twin alone:

* the declared order of the values (enum or check list) gives the initial state and a weak
  chain of consecutive transitions; values named like an end state (``cancelled``, ``rejected``,
  ``delivered`` ...) are terminal and start no chain edge;
* a timestamp attribute named after a state (``shipped_at`` for ``shipped``) on the entity or a
  directly related one is evidence that the state is *entered* at a recorded moment;
* an action whose verb inflects to a state (``approve`` -> ``approved``, ``cancelOrder`` ->
  ``cancelled``) on the entity is evidence of a transition into it -- also through a proposed
  ``same_as`` match, at half weight;
* a *history entity* -- one that references this entity and carries the same value set plus a
  timestamp -- marks the workflow as minable from data.

**Data** (:func:`mine_event_log`), given an ordered event log ``(case, state, at)``: the
directly-follows transitions with their support. Reading a history table needs a templated read
(AP6), so the twin builder does not call this yet; the function is here, tested, so the AP6
wiring is a data plumbing change rather than new logic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, Sequence

from ..domain.ausm import AusmElement, ElementKind, Provenance
from ..domain.discovery import quote_identifier
from ..domain.serialization import content_digest
from ..domain.untrusted import UntrustedText
from .normalize import _EVENT_SUFFIXES, humanize, name_tokens

__all__ = [
    "workflow_candidates",
    "mine_event_log",
    "MinedTransition",
    "MINING_EXTRACTOR",
    "TERMINAL_STATES",
]

MINING_EXTRACTOR = "process_mining"
#: End-of-life state names, as tokens. Lifecycle vocabulary, not any one domain's nouns.
TERMINAL_STATES = frozenset({
    "cancelled", "canceled", "rejected", "terminated", "closed", "completed", "complete", "done",
    "delivered", "returned", "refunded", "failed", "archived", "expired", "resolved", "deleted",
    "declined", "finished", "void", "voided",
})

_CHAIN = 0.25        # consecutive declared values
_FROM_INITIAL = 0.15  # initial -> a later value, not consecutive
_TIMESTAMP = 0.3
_ACTION = 0.3
_ACTION_VIA_MATCH = 0.15
_HISTORY = 0.1
_CAP = 0.9
_MAX_PROPERTY = 256
_MAX_LOCATOR = 512


def _inflections(verb: str) -> set[str]:
    """Past forms a state might take from a verb: approve -> approved, ship -> shipped."""
    forms = {verb, verb + "ed", verb + "d"}
    if verb.endswith("y"):
        forms.add(verb[:-1] + "ied")
    if len(verb) > 2:
        forms.add(verb + verb[-1] + "ed")  # ship -> shipped, cancel -> cancelled
    return forms


def _verbs(action: AusmElement) -> set[str]:
    out: set[str] = set()
    operation = action.properties.get("operation_id") or action.properties.get("identifier")
    if isinstance(operation, str) and name_tokens(operation):
        out.add(name_tokens(operation)[0])
    path = action.properties.get("path")
    if isinstance(path, str):
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        if len(segments) > 1:
            out.add("_".join(name_tokens(segments[-1])))
    if action.properties.get("http_method") == "DELETE":
        out.update({"delete", "cancel", "remove"})
    return out


def _state_key(value: str) -> str:
    return "_".join(name_tokens(value))


@dataclass
class _Edge:
    source: str
    target: str
    confidence: float
    evidence: list[str]
    citations: list[AusmElement]


def workflow_candidates(elements: Sequence[AusmElement]) -> list[AusmElement]:
    """WORKFLOW candidates for every status attribute with at least two declared values."""
    by_id = {e.id: e for e in elements}
    states: dict[str, list[AusmElement]] = {}
    for element in elements:
        if element.kind is ElementKind.STATE and element.parent:
            states.setdefault(element.parent, []).append(element)
    relations = [e for e in elements if e.kind is ElementKind.RELATION]
    events = [e for e in elements if e.kind is ElementKind.EVENT]
    actions = [e for e in elements if e.kind is ElementKind.ACTION]

    def neighbours(entity: str) -> set[str]:
        out = {entity}
        for rel in relations:
            if rel.properties.get("relation_type") == "same_as":
                continue
            ends = {rel.refs.get("from"), rel.refs.get("to")}
            if entity in ends:
                out.update(e for e in ends if e)
        return out

    def matched(entity: str) -> set[str]:
        out = set()
        for rel in relations:
            if rel.properties.get("relation_type") == "same_as":
                ends = {rel.refs.get("from"), rel.refs.get("to")}
                if entity in ends:
                    out.update(e for e in ends if e and e != entity)
        return out

    out: list[AusmElement] = []
    for attribute_id in sorted(states):
        attribute = by_id.get(attribute_id)
        if attribute is None or attribute.properties.get("semantic_type") != "status" or not attribute.parent:
            continue
        entity = by_id.get(attribute.parent)
        if entity is None or entity.properties.get("source_type") == "view":
            continue
        values = sorted(states[attribute_id], key=lambda s: s.properties.get("ordinal", 0))
        if len(values) < 2:
            continue
        out.extend(_workflow(attribute, entity, values, neighbours(entity.id), matched(entity.id),
                             events, actions, elements, by_id))
    return out


def _workflow(attribute, entity, values, near, matches, events, actions, elements, by_id) -> list[AusmElement]:
    src = attribute.source_id
    names = [str(v.properties["value"]) for v in values]
    keys = [_state_key(n) for n in names]
    initial = names[0]
    terminal = {n for n, k in zip(names, keys) if k in TERMINAL_STATES}

    edges: dict[tuple[str, str], _Edge] = {}

    def edge(a: str, b: str, base: float, why: str) -> _Edge:
        found = edges.get((a, b))
        if found is None:
            found = edges[(a, b)] = _Edge(a, b, base, [why], [])
        return found

    for a, b in zip(names, names[1:]):
        if a not in terminal:
            edge(a, b, _CHAIN, "declared_order")
    for b in names[1:]:
        if (initial, b) not in edges:
            edge(initial, b, _FROM_INITIAL, "from_initial")

    def boost(target: str, amount: float, why: str, citation: AusmElement) -> None:
        into = [e for e in edges.values() if e.target == target]
        for e in into:
            if why not in e.evidence:
                e.confidence += amount
                e.evidence.append(why)
            e.citations.append(citation)

    # Timestamps named after a state, on this entity or one directly related to it.
    for event in events:
        if event.refs.get("entity") not in near:
            continue
        stem = "_".join(t for t in name_tokens(str(event.properties.get("attribute") or "")) if t not in _EVENT_SUFFIXES)
        for name, key in zip(names, keys):
            if stem and stem == key and name != initial:
                boost(name, _TIMESTAMP, "timestamp", event)
    # Actions whose verb inflects to a state.
    for action in actions:
        target_entity = action.refs.get("entity")
        if target_entity == entity.id:
            amount, why = _ACTION, "action"
        elif target_entity in matches:
            amount, why = _ACTION_VIA_MATCH, "action_via_match"
        else:
            continue
        forms = set().union(*(_inflections(v) for v in _verbs(action))) if _verbs(action) else set()
        for name, key in zip(names, keys):
            if key in forms and name != initial:
                boost(name, amount, why, action)
    # A history entity: references this one, carries the same value set and a timestamp.
    history = _history_entity(attribute, entity, values, elements, by_id)
    if history is not None:
        for e in edges.values():
            e.confidence += _HISTORY
            e.evidence.append("history")

    out: list[AusmElement] = []
    suffix = attribute.locator.split(":", 1)[1]
    evidence_hash = attribute.provenance[0].evidence_hash
    best = max(e.confidence for e in edges.values()) if edges else 0.0
    workflow_props: dict[str, Any] = {
        "role": "process",
        "state_count": len(names),
        "initial": initial if len(initial) <= _MAX_PROPERTY else None,
        "states": _bounded(",".join(quote_identifier(n) for n in names)),
        "terminal": _bounded(",".join(quote_identifier(n) for n in names if n in terminal)),
        "transition_count": len(edges),
        "history_entity": history.id if history is not None and len(history.id) <= _MAX_PROPERTY else None,
    }
    workflow_confidence = round(min(_CAP, 0.4 + (0.1 if history else 0.0) + (0.2 if best > _CHAIN else 0.0)), 4)
    out.append(AusmElement(
        src, f"workflow:{suffix}", ElementKind.WORKFLOW,
        UntrustedText.of(f"{entity.label.raw()} {attribute.label.raw().lower()} lifecycle", max_chars=200),
        (Provenance(src, attribute.locator, MINING_EXTRACTOR, workflow_confidence, evidence_hash),),
        refs={"entity": entity.id, "state_attribute": attribute.id},
        properties={k: v for k, v in workflow_props.items() if v is not None},
    ))
    for (a, b), e in sorted(edges.items(), key=lambda kv: (names.index(kv[0][0]), names.index(kv[0][1]))):
        locator = f"transition:{suffix}:{quote_identifier(a)}>{quote_identifier(b)}"
        if len(locator) > _MAX_LOCATOR or len(a) > _MAX_PROPERTY or len(b) > _MAX_PROPERTY:
            continue
        confidence = round(min(_CAP, e.confidence), 4)
        provenance = [Provenance(src, attribute.locator, MINING_EXTRACTOR, confidence, evidence_hash)]
        for cited in sorted({c.id: c for c in e.citations}.values(), key=lambda c: c.id):
            if cited.source_id == src:
                provenance.append(Provenance(
                    src, cited.locator, MINING_EXTRACTOR, confidence,
                    content_digest({"cites": cited.id, "evidence": cited.provenance[0].evidence_hash}),
                ))
            else:  # through a same_as match: the provenance names the other source
                provenance.append(Provenance(
                    cited.source_id, cited.locator, MINING_EXTRACTOR, confidence, cited.provenance[0].evidence_hash,
                ))
        out.append(AusmElement(
            src, locator, ElementKind.WORKFLOW,
            UntrustedText.of(f"{humanize(a)} → {humanize(b)}", max_chars=200), tuple(provenance),
            refs={"entity": entity.id, "state_attribute": attribute.id},
            properties={
                "role": "transition", "from_state": a, "to_state": b,
                "evidence": ",".join(e.evidence), "terminal_target": b in terminal,
            },
        ))
    return out


def _history_entity(attribute, entity, values, elements, by_id) -> AusmElement | None:
    wanted = {str(v.properties["value"]) for v in values}
    candidates = []
    for rel in elements:
        if rel.kind is not ElementKind.RELATION or rel.refs.get("to") != entity.id:
            continue
        if rel.properties.get("relation_type") == "same_as":
            continue
        other = by_id.get(rel.refs.get("from", ""))
        if other is None or other.id == entity.id:
            continue
        status_values: set[str] = set()
        has_time = False
        for child in elements:
            if child.kind is ElementKind.ATTRIBUTE and child.parent == other.id:
                if child.properties.get("semantic_type") in ("timestamp",):
                    has_time = True
                if child.properties.get("semantic_type") == "status":
                    status_values |= {
                        str(s.properties["value"]) for s in elements
                        if s.kind is ElementKind.STATE and s.parent == child.id
                    }
        if has_time and status_values and status_values <= wanted and len(status_values) >= 2:
            candidates.append(other)
    return min(candidates, key=lambda e: e.id) if candidates else None


def _bounded(value: str) -> str | None:
    return value if value and len(value) <= _MAX_PROPERTY else None


# ------------------------------------------------------------------------------- from data


@dataclass(frozen=True, slots=True)
class MinedTransition:
    """A directly-follows transition observed in an event log."""

    source: str
    target: str
    support: int          # cases in which it was observed
    confidence: float     # support / cases that left ``source`` at all


def mine_event_log(events: Iterable[tuple[Hashable, str, Any]], *, min_support: int = 1) -> list[MinedTransition]:
    """Directly-follows transitions from ``(case, state, at)`` rows.

    Rows are grouped by case and ordered by ``at`` (ties keep input order); consecutive repeats of
    a state are collapsed. Confidence is the share of departures from ``source`` that went to
    ``target``, so it stays below 1 whenever a state has more than one observed exit.
    """
    cases: dict[Hashable, list[tuple[Any, int, str]]] = {}
    for index, (case, state, at) in enumerate(events):
        cases.setdefault(case, []).append((at, index, state))
    pairs: Counter[tuple[str, str]] = Counter()
    for rows in cases.values():
        trail = [state for _, _, state in sorted(rows, key=lambda r: (r[0], r[1]))]
        collapsed = [s for i, s in enumerate(trail) if i == 0 or s != trail[i - 1]]
        pairs.update(set(zip(collapsed, collapsed[1:])))
    departures: Counter[str] = Counter()
    for (a, _), n in pairs.items():
        departures[a] += n
    out = [
        MinedTransition(a, b, n, round(n / departures[a], 4))
        for (a, b), n in pairs.items() if n >= min_support
    ]
    return sorted(out, key=lambda t: (t.source, -t.support, t.target))
