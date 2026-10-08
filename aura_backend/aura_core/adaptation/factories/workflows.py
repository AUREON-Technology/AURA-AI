"""Workflow factory: read-only status workflows from process-mining candidates (AP6).

A workflow is compiled only where ``workflow.status`` was selected *and* compiled into a tool:
the workflow describes a process AURA can actually report on. Transitions below
:data:`MIN_TRANSITION_CONFIDENCE` stay in the twin as candidates but are not compiled -- a
blueprint states what a Super Admin is asked to approve, and a weak guess is not that. Running
workflows (triggers, notifications) is AP11.
"""

from __future__ import annotations

from ...domain.ausm import ElementKind
from ...domain.blueprint import BlueprintWorkflow, Transition, WorkflowKind
from ...domain.compilation import DroppedItem
from .common import TwinView, slug, unique_slug
from .tools import CompiledTool

__all__ = ["build_workflows", "MIN_TRANSITION_CONFIDENCE"]

MIN_TRANSITION_CONFIDENCE = 0.4


def build_workflows(tools: list[CompiledTool], view: TwinView) -> tuple[list[BlueprintWorkflow], list[DroppedItem]]:
    workflows: list[BlueprintWorkflow] = []
    dropped: list[DroppedItem] = []
    taken: set[str] = set()
    for compiled in tools:
        if compiled.selection.capability != "workflow.status":
            continue
        attribute = view.get(compiled.selection.target)
        entity = view.get(attribute.parent or "") if attribute else None
        if attribute is None or entity is None:
            dropped.append(DroppedItem("workflow", compiled.selection.key, "target_missing"))
            continue
        states = view.states(attribute.id)
        process = [w for w in view.of_kind(ElementKind.WORKFLOW)
                   if w.refs.get("state_attribute") == attribute.id and w.properties.get("role") == "process"]
        if len(states) < 2 or not process:
            dropped.append(DroppedItem("workflow", compiled.selection.key, "no_process"))
            continue
        by_value = {str(s.properties.get("value")): s.id for s in states}
        transitions: list[Transition] = []
        evidence = [process[0].id]
        for candidate in sorted(view.of_kind(ElementKind.WORKFLOW), key=lambda w: w.id):
            if candidate.refs.get("state_attribute") != attribute.id or candidate.properties.get("role") != "transition":
                continue
            if candidate.confidence < MIN_TRANSITION_CONFIDENCE:
                continue
            source = by_value.get(str(candidate.properties.get("from_state")))
            target = by_value.get(str(candidate.properties.get("to_state")))
            if source and target:
                transitions.append(Transition(source, target))
                evidence.append(candidate.id)
        workflows.append(BlueprintWorkflow(
            id=unique_slug(slug("wf", view.identifier(entity), view.identifier(attribute)), taken),
            kind=WorkflowKind.READ_STATUS, entity=entity.id, state_attribute=attribute.id,
            states=tuple(s.id for s in states), evidence=tuple(evidence), transitions=tuple(transitions),
        ))
    return workflows, dropped
