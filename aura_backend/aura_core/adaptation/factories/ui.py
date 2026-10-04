"""UI factory: the UI Capability Manifest -- one view per tool, navigation per entity (ADR-027).

Views come from the closed vocabulary in :class:`~aura_core.domain.blueprint.ViewType` and bind
a tool plus the output fields they show; a client renders them, nothing in them executes. v1 is
read-only: ``approval_queue`` appears only when a capability that drafts something compiled.
"""

from __future__ import annotations

from ...domain.blueprint import NavItem, UiManifest, UiView, ViewType
from ...domain.untrusted import UntrustedText
from .common import TwinView, slug, unique_slug
from .tools import CompiledTool

__all__ = ["build_ui", "MAX_VIEW_FIELDS"]

MAX_VIEW_FIELDS = 8
_NAV_PREFERENCE = (ViewType.ENTITY_LIST, ViewType.SEARCH, ViewType.ENTITY_DETAIL, ViewType.METRIC_CARD, ViewType.TIMELINE)
_TITLES = {
    ViewType.ENTITY_LIST: "list", ViewType.ENTITY_DETAIL: "record", ViewType.SEARCH: "search",
    ViewType.METRIC_CARD: "summary", ViewType.TIMELINE: "timeline",
}


def build_ui(tools: list[CompiledTool], view: TwinView) -> UiManifest:
    views: list[UiView] = []
    taken: set[str] = set()
    by_entity: dict[str, list[UiView]] = {}
    for compiled in tools:
        kind = ViewType(compiled.view)
        fields = tuple(sorted(compiled.tool.output_fields() or ()))[:MAX_VIEW_FIELDS]
        entity = view.get(compiled.entity) if compiled.entity else None
        subject = entity.label.raw() if entity is not None else compiled.tool.name
        ui_view = UiView(
            id=unique_slug(slug("v", compiled.tool.name), taken), type=kind,
            title=UntrustedText.of(f"{subject} — {_TITLES[kind]}", max_chars=200),
            tool=compiled.tool.name, fields=fields,
        )
        views.append(ui_view)
        if compiled.entity:
            by_entity.setdefault(compiled.entity, []).append(ui_view)
    views.append(UiView("assistant", ViewType.CHAT, UntrustedText("Assistant")))
    navigation: list[NavItem] = []
    nav_taken: set[str] = set()
    for entity_id in sorted(by_entity):
        entity = view.get(entity_id)
        candidates = by_entity[entity_id]
        chosen = min(candidates, key=lambda v: (_NAV_PREFERENCE.index(v.type) if v.type in _NAV_PREFERENCE else 99, v.id))
        navigation.append(NavItem(
            id=unique_slug(slug(entity.source_id, view.identifier(entity)), nav_taken),
            label=entity.label, view=chosen.id,
        ))
    navigation.append(NavItem("assistant", UntrustedText("Assistant"), "assistant"))
    return UiManifest(navigation=tuple(navigation), views=tuple(views))
