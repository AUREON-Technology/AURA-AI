"""The AURA settings view: every setting, the variable that sets it, and its current value.

Built from ``aura_core/config/settings.py`` itself, so the console can never describe a setting
that does not exist or miss one that does:

* the variable name comes from the ``from_env`` call that reads it (``_int("KNOWLEDGE_TOP_K", 2,
  minimum=1, maximum=50)`` -> ``AURA_KNOWLEDGE_TOP_K``, with its default and bounds), following
  local variables and helpers through their assignments;
* the description is the ``#:`` comment above the field;
* the type and default come from the dataclass field;
* the current value is the running process's effective value (``AuraSettings.as_dict``).

A secret's value is never included: only whether it is set. A handful of settings are read in a
way the walk cannot follow; they are named in ``_EXPLICIT`` and a test fails if any setting is
left without a variable, so a new setting cannot silently drop out of the view.

Per-provider settings (``AURA_PROVIDER_<NAME>_*``) are listed once per configured provider.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping, Sequence

from ..domain.control import PROTECTED_VARIABLES, is_secret_variable

__all__ = ["CatalogEntry", "build_catalog", "variable_map"]

#: The readers ``settings.py`` uses; their first argument is the variable name without ``AURA_``.
_READERS = frozenset({"_env", "_bool", "_int", "_float", "_list", "_bool_aliased", "_choice"})

#: Settings read in ways the walk does not follow (a helper that builds the name). Keyed by
#: ``section.field``; the test that requires every setting to be mapped keeps this honest.
_EXPLICIT: Mapping[str, str] = {
    "llm.embedding_query_prefix": "AURA_EMBEDDING_QUERY_PREFIX",
    "llm.embedding_document_prefix": "AURA_EMBEDDING_DOCUMENT_PREFIX",
}

#: Sections that are not settings an operator sets (computed at build time, or per provider).
_SKIP_SECTIONS = frozenset({"release"})


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    section: str
    field: str
    variable: str
    type: str
    default: Any
    description: str
    secret: bool
    protected: bool
    #: Whether the variable is present in the process environment (else the default applies).
    set: bool
    #: The effective value; ``None`` for a secret.
    value: Any
    minimum: Any = None
    maximum: Any = None
    #: Always true today: settings are read once at boot (``load_settings``), so a change needs a
    #: redeploy. Kept explicit so the console says so rather than implying a live edit.
    requires_redeploy: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section, "field": self.field, "variable": self.variable,
            "type": self.type, "default": _jsonable(self.default),
            "description": self.description, "secret": self.secret,
            "protected": self.protected, "set": self.set,
            "value": None if self.secret else _jsonable(self.value),
            "minimum": self.minimum, "maximum": self.maximum,
            "requires_redeploy": self.requires_redeploy,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# -- reading settings.py ------------------------------------------------------------------

def _settings_module() -> Any:
    from ..config import settings as module

    return module


def _reader_call(node: ast.AST, assigns: Mapping[str, ast.AST], depth: int = 0) -> ast.Call | None:
    """The first reader call in ``node``, following local names through their assignments."""
    if depth > 4:
        return None
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            and sub.func.id in _READERS and sub.args
            and isinstance(sub.args[0], (ast.Constant, ast.JoinedStr))
        ):
            return sub
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in assigns:
            found = _reader_call(assigns[sub.id], assigns, depth + 1)
            if found is not None:
                return found
    return None


def _constant(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _name_of(call: ast.Call) -> str:
    first = call.args[0]
    if isinstance(first, ast.Constant):
        return f"AURA_{first.value}"
    # f"{p}_BASE_URL" inside ProviderConfig.from_env: keep the constant tail.
    tail = "".join(v.value for v in first.values if isinstance(v, ast.Constant))
    return f"AURA_PROVIDER_<NAME>{tail}"


@lru_cache(maxsize=1)
def _class_index() -> dict[str, dict[str, dict[str, Any]]]:
    """``{class name: {field: {variable, minimum, maximum, description}}}`` from the source."""
    module = _settings_module()
    source = inspect.getsource(module)
    tree = ast.parse(source)
    lines = source.splitlines()
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        entries: dict[str, dict[str, Any]] = {}
        # Descriptions: the contiguous `#:` comment block above each annotated field.
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                comment: list[str] = []
                line = stmt.lineno - 2
                while line >= 0 and lines[line].strip().startswith("#:"):
                    comment.insert(0, lines[line].strip()[2:].strip())
                    line -= 1
                entries.setdefault(stmt.target.id, {})["description"] = " ".join(comment)
        for fn in (s for s in cls.body if isinstance(s, ast.FunctionDef) and s.name == "from_env"):
            assigns = {
                t.id: a.value
                for a in ast.walk(fn) if isinstance(a, ast.Assign)
                for t in a.targets if isinstance(t, ast.Name)
            }
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "cls"):
                    continue
                for keyword in call.keywords:
                    reader = _reader_call(keyword.value, assigns)
                    if reader is None or keyword.arg is None:
                        continue
                    bounds = {k.arg: _constant(k.value) for k in reader.keywords}
                    entries.setdefault(keyword.arg, {}).update(
                        variable=_name_of(reader),
                        minimum=bounds.get("minimum"),
                        maximum=bounds.get("maximum"),
                    )
        index[cls.name] = entries
    return index


def variable_map() -> dict[str, str]:
    """``{"section.field": "AURA_..."}`` for every top-level setting (tests use this)."""
    from ..config.settings import AuraSettings

    out: dict[str, str] = {}
    index = _class_index()
    defaults = AuraSettings()
    for top in fields(AuraSettings):
        section = getattr(defaults, top.name)
        if not is_dataclass(section) or top.name in _SKIP_SECTIONS:
            continue
        info = index.get(type(section).__name__, {})
        for f in fields(section):
            key = f"{top.name}.{f.name}"
            variable = (
                _EXPLICIT.get(key) or info.get(f.name, {}).get("variable")
                or _named_secret(f.name, getattr(section, f.name))
            )
            if variable:
                out[key] = variable
    return out


def _named_secret(field_name: str, value: Any) -> str | None:
    """A ``*_env`` field holding the *name* of a secret's variable (``smtp_password_env =
    "AURA_SMTP_PASSWORD"``): the secret is that variable, listed write-only."""
    if field_name.endswith("_env") and isinstance(value, str) and value.startswith("AURA_"):
        return value
    return None


def _type_name(annotation: Any) -> str:
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))
    return str(text).replace("typing.", "")


def _default(f: Any) -> Any:
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:  # type: ignore[misc]
        try:
            return f.default_factory()  # type: ignore[misc]
        except Exception:
            return None
    return None


def build_catalog(
    settings: Any, environ: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Groups of entries, one group per settings section, in declaration order.

    ``environ`` is the process environment (stage 1) or the platform's variables (stage 2): it
    decides ``set`` and, for a secret, nothing else.
    """
    index = _class_index()
    effective = settings.as_dict(redact=True)
    groups: list[dict[str, Any]] = []
    for top in fields(type(settings)):
        section = getattr(settings, top.name)
        if not is_dataclass(section) or top.name in _SKIP_SECTIONS:
            continue
        info = index.get(type(section).__name__, {})
        values = effective.get(top.name, {})
        entries: list[dict[str, Any]] = []
        for f in fields(section):
            if top.name == "llm" and f.name == "providers":
                continue  # listed per provider below
            meta = info.get(f.name, {})
            named = _named_secret(f.name, getattr(section, f.name))
            variable = _EXPLICIT.get(f"{top.name}.{f.name}") or meta.get("variable") or named
            if not variable:
                continue
            raw = environ.get(variable)
            entries.append(CatalogEntry(
                section=top.name, field=f.name.removesuffix("_env") if named else f.name,
                variable=variable, type="secret" if named else _type_name(f.type),
                default=None if named else _default(f), description=meta.get("description", ""),
                secret=bool(named) or is_secret_variable(variable, raw),
                protected=variable in PROTECTED_VARIABLES,
                set=raw is not None, value=None if named else values.get(f.name),
                minimum=meta.get("minimum"), maximum=meta.get("maximum"),
            ).as_dict())
        groups.append({"section": top.name, "settings": entries})
    groups.insert(1, {"section": "providers", "settings": _provider_entries(settings, environ, index)})
    return groups


def _provider_entries(
    settings: Any, environ: Mapping[str, str], index: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    from ..config.settings import ProviderConfig

    info = index.get("ProviderConfig", {})
    entries: list[dict[str, Any]] = []
    for provider in settings.llm.providers:
        prefix = f"AURA_PROVIDER_{provider.name.upper().replace('-', '_')}"
        for f in fields(ProviderConfig):
            meta = info.get(f.name, {})
            template = meta.get("variable")
            if not template or f.name == "name":
                continue
            variable = template.replace("AURA_PROVIDER_<NAME>", prefix)
            raw = environ.get(variable)
            value = getattr(provider, f.name, None)
            entries.append(CatalogEntry(
                section=f"provider:{provider.name}", field=f.name, variable=variable,
                type=_type_name(f.type), default=_default(f),
                description=meta.get("description", ""),
                secret=is_secret_variable(variable, raw), protected=False,
                set=raw is not None, value=value,
                minimum=meta.get("minimum"), maximum=meta.get("maximum"),
            ).as_dict())
        key_var = getattr(provider, "api_key_env", "") or ""
        if key_var:
            # The key itself: the variable the slot names. Never a field, never shown.
            entries.append(CatalogEntry(
                section=f"provider:{provider.name}", field="api_key", variable=key_var,
                type="secret", default=None, description="The provider's API key.",
                secret=True, protected=False, set=bool(environ.get(key_var)), value=None,
            ).as_dict())
    return entries


def flatten(groups: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [entry for group in groups for entry in group["settings"]]
