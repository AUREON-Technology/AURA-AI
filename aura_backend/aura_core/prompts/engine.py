"""Prompt engine: versioned templates, rendering, and evaluation hooks.

Today prompts are string literals concatenated at runtime (``services/agent.py:15-40`` and
``:214-221``). Changing one requires a code review, a build, and a deploy; there is no version
history, no way to A/B two variants, and no test that a wording change did not break tool
calling. TD-11.

Here a prompt is data:

* a :class:`PromptTemplate` has an id, a semantic version, declared variables, and the model
  capabilities it assumes;
* rendering is explicit and **fails on a missing variable** rather than emitting the literal
  ``{placeholder}`` into a production prompt;
* the registry can hold several versions of one template and select by policy, which is what
  makes prompt A/B testing and instant rollback possible;
* templates render deterministically, so ``tests/prompts/`` can assert exact output and catch
  accidental edits.

Deliberately not Jinja2. The template surface needed here is variable substitution, optional
blocks, and loops over a list of strings. Jinja brings arbitrary expression evaluation and
filters into the one place in the system where untrusted values are interpolated -- a template
engine with logic is an unnecessary attack surface for a ~100-line feature.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from ..domain.errors import NotFoundError, ValidationError

__all__ = ["PromptTemplate", "PromptVariable", "RenderedPrompt", "PromptRegistry"]

# {{ name }} -- doubled braces so single braces (JSON examples, code) pass through untouched.
_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
# {{#name}} ... {{/name}} -- rendered only when `name` is non-empty.
_SECTION = re.compile(
    r"\{\{#\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}(.*?)\{\{/\s*\1\s*\}\}", re.DOTALL
)


@dataclass(frozen=True, slots=True)
class PromptVariable:
    name: str
    description: str
    required: bool = True
    default: str = ""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A versioned prompt.

    ``checksum`` is derived from the body, so a template edited without a version bump is
    detectable -- the failure mode where "v1.2.0" means two different things in two deployments
    is otherwise invisible.
    """

    id: str
    version: str
    body: str
    description: str = ""
    variables: tuple[PromptVariable, ...] = ()
    tags: tuple[str, ...] = ()
    requires_tools: bool = False
    min_quality_tier: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    _VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

    def __post_init__(self) -> None:
        if not self.id:
            raise ValidationError("PromptTemplate.id must be non-empty")
        if not self._VERSION_RE.match(self.version):
            raise ValidationError(
                f"prompt {self.id!r} version must be semantic (x.y.z), got {self.version!r}"
            )
        declared = {v.name for v in self.variables}
        used = set(_VARIABLE.findall(self.body)) | set(m[0] for m in _SECTION.findall(self.body))
        undeclared = used - declared
        if undeclared:
            # Catching this at construction means a typo'd placeholder fails at import, not in
            # production with a literal "{{ usre_name }}" in the model's instructions.
            raise ValidationError(
                f"prompt {self.id!r} uses undeclared variables",
                details={"undeclared": sorted(undeclared), "declared": sorted(declared)},
            )

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def required_variables(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variables if v.required)

    def render(self, values: Mapping[str, Any] | None = None) -> "RenderedPrompt":
        supplied = dict(values or {})
        resolved: dict[str, str] = {}
        missing: list[str] = []

        for variable in self.variables:
            if variable.name in supplied and supplied[variable.name] is not None:
                raw = supplied[variable.name]
                if isinstance(raw, (list, tuple)):
                    # Lists render as bullets -- the common case (memories, citations, criteria)
                    # and one less formatting decision at each call site.
                    resolved[variable.name] = "\n".join(f"- {item}" for item in raw if str(item).strip())
                else:
                    resolved[variable.name] = str(raw)
            elif variable.required:
                missing.append(variable.name)
            else:
                resolved[variable.name] = variable.default

        if missing:
            raise ValidationError(
                f"prompt {self.key} is missing required variables",
                details={"missing": sorted(missing)},
            )

        def render_sections(text: str) -> str:
            def replace_section(match: re.Match[str]) -> str:
                name, inner = match.group(1), match.group(2)
                return inner if resolved.get(name, "").strip() else ""

            # Loop so nested sections resolve; bounded to avoid a pathological template looping
            # forever on malformed markers.
            for _ in range(5):
                new_text = _SECTION.sub(replace_section, text)
                if new_text == text:
                    break
                text = new_text
            return text

        body = render_sections(self.body)
        body = _VARIABLE.sub(lambda m: resolved.get(m.group(1), ""), body)
        # Collapse the blank-line runs left behind by omitted optional sections.
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

        return RenderedPrompt(
            template_id=self.id,
            version=self.version,
            checksum=self.checksum,
            text=body,
            variables_used=tuple(sorted(k for k, v in resolved.items() if v)),
        )

    def bumped(self, body: str, *, level: str = "patch") -> "PromptTemplate":
        """Return a new version of this template. Used by the admin prompt editor."""
        major, minor, patch = (int(p) for p in self.version.split("."))
        if level == "major":
            major, minor, patch = major + 1, 0, 0
        elif level == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        return replace(self, body=body, version=f"{major}.{minor}.{patch}")


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """A concrete prompt plus the provenance needed to reproduce it.

    ``template_id``/``version`` land in :class:`~aura_core.domain.telemetry.TurnTelemetry`, so a
    quality regression can be traced to the prompt change that caused it -- impossible today,
    since prompts leave no trace in any record.
    """

    template_id: str
    version: str
    checksum: str
    text: str
    variables_used: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.text

    @property
    def approx_tokens(self) -> int:
        return max(1, (len(self.text) + 3) // 4)


class PromptRegistry:
    """Holds templates, resolves versions, and enforces uniqueness.

    Version resolution: ``get("id")`` returns the active version (the highest registered unless
    one is pinned); ``get("id", version="1.2.0")`` returns exactly that. Pinning is what makes
    rollback a configuration change rather than a deploy.
    """

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, PromptTemplate]] = {}
        self._pinned: dict[str, str] = {}

    def register(self, template: PromptTemplate, *, overwrite: bool = False) -> None:
        versions = self._templates.setdefault(template.id, {})
        existing = versions.get(template.version)
        if existing is not None and not overwrite:
            if existing.checksum != template.checksum:
                # Two different bodies claiming the same version is the failure this check
                # exists to make loud.
                raise ValidationError(
                    f"prompt {template.key} already registered with a different body",
                    details={"existing_checksum": existing.checksum, "new_checksum": template.checksum},
                )
            return
        versions[template.version] = template

    def register_all(self, templates: Iterable[PromptTemplate]) -> None:
        for template in templates:
            self.register(template)

    def pin(self, template_id: str, version: str) -> None:
        if version not in self._templates.get(template_id, {}):
            raise NotFoundError(
                f"cannot pin unknown prompt version {template_id}@{version}",
                details={"available": self.versions(template_id)},
            )
        self._pinned[template_id] = version

    def unpin(self, template_id: str) -> None:
        self._pinned.pop(template_id, None)

    @staticmethod
    def _sort_key(version: str) -> tuple[int, ...]:
        return tuple(int(p) for p in version.split("."))

    def versions(self, template_id: str) -> list[str]:
        return sorted(self._templates.get(template_id, {}), key=self._sort_key)

    def get(self, template_id: str, *, version: str | None = None) -> PromptTemplate:
        versions = self._templates.get(template_id)
        if not versions:
            raise NotFoundError(
                f"unknown prompt template {template_id!r}",
                details={"known": sorted(self._templates)},
            )
        wanted = version or self._pinned.get(template_id)
        if wanted:
            template = versions.get(wanted)
            if template is None:
                raise NotFoundError(
                    f"unknown prompt version {template_id}@{wanted}",
                    details={"available": self.versions(template_id)},
                )
            return template
        return versions[max(versions, key=self._sort_key)]

    def render(
        self, template_id: str, values: Mapping[str, Any] | None = None, *, version: str | None = None
    ) -> RenderedPrompt:
        return self.get(template_id, version=version).render(values)

    def ids(self) -> list[str]:
        return sorted(self._templates)

    def describe(self) -> list[dict[str, Any]]:
        """Inventory for the admin API and for prompt-regression tests."""
        return [
            {
                "id": template_id,
                "versions": self.versions(template_id),
                "active": self.get(template_id).version,
                "pinned": self._pinned.get(template_id),
                "checksum": self.get(template_id).checksum,
                "description": self.get(template_id).description,
                "variables": [v.name for v in self.get(template_id).variables],
            }
            for template_id in sorted(self._templates)
        ]

    def __len__(self) -> int:
        return sum(len(v) for v in self._templates.values())

    def __contains__(self, template_id: object) -> bool:
        return template_id in self._templates


def variables(*specs: tuple[str, str] | tuple[str, str, bool]) -> tuple[PromptVariable, ...]:
    """Terse declaration helper: ``variables(("name", "desc"), ("opt", "desc", False))``."""
    result = []
    for spec in specs:
        name, description = spec[0], spec[1]
        required = bool(spec[2]) if len(spec) > 2 else True
        result.append(PromptVariable(name=name, description=description, required=required))
    return tuple(result)


def collect_used_variables(body: str) -> Sequence[str]:
    return sorted(set(_VARIABLE.findall(body)) | {m[0] for m in _SECTION.findall(body)})
