"""OpenAPI validation, schema-level breaking-change classification and test generation (ADR-044, row 93).

``ApiContractValidator`` diffs *route sets* of JSON specs. This adds, over the same caller-supplied text
(JSON **or YAML**):

* ``validate_spec`` -- structural OpenAPI 3.x / Swagger 2 checks with a JSON pointer for each problem:
  version and ``info``, paths that do not start with ``/``, ``{path}`` parameters that are not declared,
  duplicate ``operationId``, operations without responses, ``$ref`` pointers that do not resolve,
  ``required`` properties a schema never defines, unknown ``type`` values, undefined security schemes.
* ``classify_changes`` -- every difference between two specs, classified ``breaking`` / ``non_breaking``
  / ``deprecation``, including the *schema* level the route diff cannot see: request vs response
  direction (adding a required request property breaks callers; removing a response property breaks
  consumers; the reverse does not), changed types, removed enum values, newly required parameters,
  removed success responses. The recommended semver bump follows from the classes found.
* ``generate_tests`` -- an integration-test module (text; nothing is run) with one test per operation:
  it calls the endpoint on ``BASE_URL`` and asserts a documented status and the response's required
  properties and types.

YAML is read with ``yaml.safe_load`` only, after a size cap and an alias-count cap (a YAML "billion
laughs" document is refused instead of expanded). Nothing is fetched: remote ``$ref`` is reported as
unresolved, never followed.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["load_spec", "as_json_text", "validate_spec", "classify_changes", "generate_tests"]

_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
_MAX_ALIASES = 50
_MAX_DEPTH = 8
_MAX_OPS = 100


def load_spec(text: str) -> tuple[dict[str, Any] | None, str | None, str]:
    """``(spec, error, format)`` from JSON or YAML text."""
    if not isinstance(text, str) or not text.strip():
        return None, "spec is empty", "none"
    body = text.lstrip("﻿").strip()
    try:
        data = json.loads(body)
        fmt = "json"
    except ValueError as json_error:
        if re.match(r"\s*[\[{]", body):
            return None, f"JSON parse error: {json_error}", "json"
        if body.count("*") + body.count("&") > _MAX_ALIASES * 2 and re.search(r"[&*]\w+", body):
            aliases = len(re.findall(r"(?<![\w\"'])[&*][A-Za-z_]\w*", body))
            if aliases > _MAX_ALIASES:
                return None, f"YAML has {aliases} anchors/aliases; more than {_MAX_ALIASES} is refused", "yaml"
        try:
            import yaml  # lazy: PyYAML is only needed for YAML specs

            data = yaml.safe_load(body)
            fmt = "yaml"
        except Exception as exc:  # noqa: BLE001 - any parser error is the caller's bad input
            return None, f"neither JSON nor YAML: {type(exc).__name__}: {str(exc)[:200]}", "yaml"
    except RecursionError:
        return None, "spec is nested too deeply", "json"
    if not isinstance(data, dict):
        return None, "spec root must be an object", fmt
    return data, None, fmt


def as_json_text(text: str | None) -> str | None:
    """YAML spec text re-serialised as JSON so the JSON-only legacy checks accept it; else unchanged."""
    if not isinstance(text, str):
        return text
    spec, error, fmt = load_spec(text)
    return json.dumps(spec, default=str) if spec is not None and fmt == "yaml" else text


def _resolve(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _deref(spec: dict[str, Any], node: Any, seen: tuple[str, ...] = ()) -> Any:
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        if ref in seen:
            return {}
        seen = (*seen, ref)
        target = _resolve(spec, ref)
        if target is None:
            return {}
        node = target
    return node


def _ops(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    paths = spec.get("paths")
    for path, item in (paths.items() if isinstance(paths, dict) else []):
        item = _deref(spec, item)
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                merged = {**op, "_path_params": item.get("parameters") or []}
                out[(method, str(path))] = merged
    return out


def _params(spec: dict[str, Any], op: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for source in (op.get("_path_params"), op.get("parameters")):
        for raw in source if isinstance(source, list) else []:
            p = _deref(spec, raw)
            if isinstance(p, dict) and p.get("name") is not None:
                found[(str(p.get("in", "")), str(p["name"]))] = p
    return found


def _schema_of(spec: dict[str, Any], holder: Any) -> Any:
    holder = _deref(spec, holder)
    if not isinstance(holder, dict):
        return None
    if "schema" in holder:  # parameter / swagger2 response / media type
        return _deref(spec, holder["schema"])
    content = holder.get("content")
    if isinstance(content, dict) and content:
        media = content.get("application/json") or next(iter(content.values()))
        media = _deref(spec, media)
        if isinstance(media, dict) and "schema" in media:
            return _deref(spec, media["schema"])
    return None


# --- validation -----------------------------------------------------------------------------


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def err(pointer: str, message: str) -> None:
        errors.append({"at": pointer, "message": message})

    def warn(pointer: str, message: str) -> None:
        warnings.append({"at": pointer, "message": message})

    version = str(spec.get("openapi") or spec.get("swagger") or "")
    if not version:
        err("/", "missing 'openapi' (3.x) or 'swagger' (2.0) version field")
    elif not re.match(r"^(3\.\d+\.\d+|2\.0)$", version):
        warn("/openapi", f"unrecognised version '{version}'")
    info = spec.get("info")
    if not isinstance(info, dict):
        err("/info", "missing 'info' object")
    else:
        for field in ("title", "version"):
            if not info.get(field):
                err(f"/info/{field}", f"info.{field} is required")
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        err("/paths", "missing or empty 'paths'")
        paths = {}
    schemes = set()
    components = spec.get("components") if isinstance(spec.get("components"), dict) else {}
    if isinstance(components.get("securitySchemes"), dict):
        schemes |= set(components["securitySchemes"])
    if isinstance(spec.get("securityDefinitions"), dict):
        schemes |= set(spec["securityDefinitions"])
    operation_ids: dict[str, str] = {}
    normalised: dict[str, str] = {}
    for path, item in paths.items():
        base = f"/paths/{path.replace('/', '~1')}"
        if not str(path).startswith("/"):
            err(base, f"path '{path}' must start with '/'")
        shape = re.sub(r"\{[^}]+\}", "{}", str(path))
        if shape in normalised and normalised[shape] != path:
            err(base, f"path '{path}' is ambiguous with '{normalised[shape]}' (same shape)")
        normalised[shape] = str(path)
        item = _deref(spec, item)
        if not isinstance(item, dict):
            err(base, "path item must be an object")
            continue
        declared_path_params = {
            str(p.get("name")) for p in (_deref(spec, x) for x in (item.get("parameters") or []))
            if isinstance(p, dict) and p.get("in") == "path"
        }
        has_op = False
        for method in _METHODS:
            op = item.get(method)
            if op is None:
                continue
            has_op = True
            where = f"{base}/{method}"
            if not isinstance(op, dict):
                err(where, "operation must be an object")
                continue
            if not isinstance(op.get("responses"), dict) or not op["responses"]:
                err(f"{where}/responses", "operation has no responses")
            else:
                for code, response in op["responses"].items():
                    if not re.fullmatch(r"[1-5](?:\d\d|XX)|default", str(code)):
                        err(f"{where}/responses/{code}", f"'{code}' is not a status code, 'NXX' or 'default'")
                    r = _deref(spec, response)
                    if isinstance(r, dict) and not r.get("description") and version.startswith("3"):
                        err(f"{where}/responses/{code}/description", "response description is required")
            op_ids = op.get("operationId")
            if op_ids is not None:
                if op_ids in operation_ids:
                    err(f"{where}/operationId", f"operationId '{op_ids}' is already used by {operation_ids[op_ids]}")
                operation_ids[str(op_ids)] = f"{method.upper()} {path}"
            params = _params(spec, {"_path_params": item.get("parameters"), "parameters": op.get("parameters")})
            in_path = {n for (loc, n) in params if loc == "path"}
            for needed in re.findall(r"\{([^}]+)\}", str(path)):
                if needed not in in_path and needed not in declared_path_params:
                    err(f"{where}/parameters", f"path parameter '{needed}' of '{path}' is not declared")
            for (loc, name), p in params.items():
                if loc == "path" and p.get("required") is not True:
                    err(f"{where}/parameters/{name}", f"path parameter '{name}' must have required: true")
            if method in ("get", "head", "delete") and op.get("requestBody") is not None:
                warn(f"{where}/requestBody", f"{method.upper()} with a request body is not portable")
            for requirement in op.get("security") or []:
                for name in requirement if isinstance(requirement, dict) else []:
                    if schemes and name not in schemes:
                        err(f"{where}/security", f"security scheme '{name}' is not defined")
        if not has_op:
            warn(base, "path item has no operations")

    schema_keys = {"schema", "schemas", "properties", "items", "additionalProperties", "allOf", "oneOf", "anyOf", "not"}

    def walk(node: Any, pointer: str, in_schema: bool = False, depth: int = 0) -> None:
        if depth > 30:
            return
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                if not ref.startswith("#/"):
                    warn(pointer, f"external $ref '{ref}' is not followed")
                elif _resolve(spec, ref) is None:
                    err(pointer, f"$ref '{ref}' does not resolve")
            kind = node.get("type")
            if in_schema and isinstance(kind, str) and kind not in _TYPES | {"file"}:
                err(f"{pointer}/type", f"unknown type '{kind}'")
            props, required = node.get("properties"), node.get("required")
            if in_schema and isinstance(props, dict) and isinstance(required, list):
                for name in required:
                    if name not in props and "allOf" not in node and "$ref" not in node:
                        err(f"{pointer}/required", f"required property '{name}' is not defined in properties")
            for key, value in node.items():
                # inside `properties`/`schemas` the keys are names, each value a schema
                child_schema = in_schema or key in schema_keys
                walk(value, f"{pointer}/{str(key).replace('/', '~1')}", child_schema, depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{pointer}/{index}", in_schema, depth + 1)

    walk(spec, "")
    return {"valid": not errors, "errors": errors[:100], "warnings": warnings[:100],
            "error_count": len(errors), "warning_count": len(warnings), "openapi_version": version or None,
            "operations": len(_ops(spec))}


# --- change classification -----------------------------------------------------------------


class _Diff:
    def __init__(self) -> None:
        self.changes: list[dict[str, str]] = []

    def add(self, classification: str, kind: str, where: str, message: str, direction: str = "") -> None:
        self.changes.append({"classification": classification, "kind": kind, "at": where,
                             "message": message, **({"direction": direction} if direction else {})})


def _type_of(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "any"
    t = schema.get("type")
    if isinstance(t, list):
        return "|".join(sorted(map(str, t)))
    if t:
        return str(t)
    return "object" if "properties" in schema else "array" if "items" in schema else "any"


def _diff_schema(spec_old: dict, spec_new: dict, old: Any, new: Any, where: str, direction: str,
                 out: _Diff, depth: int = 0, trail: tuple[int, int] = ()) -> None:
    old, new = _deref(spec_old, old), _deref(spec_new, new)
    if depth > _MAX_DEPTH or not isinstance(old, dict) or not isinstance(new, dict):
        return
    key = (id(old), id(new))
    if key in trail:
        return
    trail = (*trail, key)
    request = direction == "request"
    t_old, t_new = _type_of(old), _type_of(new)
    if t_old != t_new and "any" not in (t_old, t_new):
        out.add("breaking", "type_changed", where, f"type changed from {t_old} to {t_new}", direction)
        return
    old_enum, new_enum = old.get("enum"), new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [v for v in old_enum if v not in new_enum]
        added = [v for v in new_enum if v not in old_enum]
        if removed:
            out.add("breaking" if request else "breaking", "enum_value_removed", where,
                    f"enum value(s) {removed} removed" + ("" if request else " (a client may still expect them)"), direction)
        if added:
            out.add("non_breaking" if request else "breaking", "enum_value_added", where,
                    f"enum value(s) {added} added" + ("" if request else " (strict clients may not handle new values)"), direction)
    elif isinstance(new_enum, list) and "enum" not in old and request:
        out.add("breaking", "enum_added", where, f"request values are now restricted to {new_enum}", direction)
    for bound, bad_when_lower in (("maxLength", True), ("maximum", True), ("maxItems", True),
                                  ("minLength", False), ("minimum", False), ("minItems", False)):
        a, b = old.get(bound), new.get(bound)
        if request and isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            tightened = b < a if bad_when_lower else b > a
            if tightened:
                out.add("breaking", "constraint_tightened", where, f"{bound} tightened from {a} to {b}", direction)
        elif request and a is None and isinstance(b, (int, float)) and not isinstance(b, bool):
            out.add("breaking", "constraint_added", where, f"{bound}={b} newly enforced", direction)
    props_old = old.get("properties") if isinstance(old.get("properties"), dict) else {}
    props_new = new.get("properties") if isinstance(new.get("properties"), dict) else {}
    req_old = set(old.get("required") or []) if isinstance(old.get("required"), list) else set()
    req_new = set(new.get("required") or []) if isinstance(new.get("required"), list) else set()
    for name in sorted(set(props_old) - set(props_new)):
        out.add("non_breaking" if request else "breaking", "property_removed", f"{where}/{name}",
                f"property '{name}' removed" + ("" if request else " (consumers reading it break)"), direction)
    for name in sorted(set(props_new) - set(props_old)):
        if request and name in req_new:
            out.add("breaking", "required_property_added", f"{where}/{name}",
                    f"new required request property '{name}'", direction)
        else:
            out.add("non_breaking", "property_added", f"{where}/{name}", f"property '{name}' added", direction)
    for name in sorted(req_new - req_old):
        if name in props_old and request:
            out.add("breaking", "property_became_required", f"{where}/{name}",
                    f"request property '{name}' became required", direction)
    for name in sorted(req_old - req_new):
        if name in props_old and not request:
            out.add("breaking", "response_property_optional", f"{where}/{name}",
                    f"response property '{name}' is no longer guaranteed", direction)
    for name in sorted(set(props_old) & set(props_new)):
        _diff_schema(spec_old, spec_new, props_old[name], props_new[name], f"{where}/{name}", direction, out, depth + 1, trail)
    if "items" in old and "items" in new:
        _diff_schema(spec_old, spec_new, old["items"], new["items"], f"{where}[]", direction, out, depth + 1, trail)
    if not old.get("nullable") and new.get("nullable") and not request:
        out.add("breaking", "became_nullable", where, "response value can now be null", direction)


def classify_changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = _Diff()
    old_ops, new_ops = _ops(old), _ops(new)
    for (method, path) in sorted(set(old_ops) - set(new_ops)):
        gone = path not in {p for (_, p) in new_ops}
        out.add("breaking", "path_removed" if gone else "operation_removed", f"{method.upper()} {path}",
                f"{'path' if gone else 'operation'} {method.upper()} {path} removed")
    for (method, path) in sorted(set(new_ops) - set(old_ops)):
        out.add("non_breaking", "operation_added", f"{method.upper()} {path}", f"operation {method.upper()} {path} added")
    for key in sorted(set(old_ops) & set(new_ops)):
        method, path = key
        where = f"{method.upper()} {path}"
        o, n = old_ops[key], new_ops[key]
        if not o.get("deprecated") and n.get("deprecated"):
            out.add("deprecation", "deprecated", where, f"{where} is now deprecated")
        po, pn = _params(old, o), _params(new, n)
        for name in sorted(set(pn) - set(po)):
            required = pn[name].get("required") is True
            out.add("breaking" if required else "non_breaking", "parameter_added", f"{where} {name[0]}:{name[1]}",
                    f"{'required' if required else 'optional'} {name[0]} parameter '{name[1]}' added", "request")
        for name in sorted(set(po) - set(pn)):
            out.add("non_breaking", "parameter_removed", f"{where} {name[0]}:{name[1]}",
                    f"{name[0]} parameter '{name[1]}' removed (callers sending it may be rejected by strict servers)", "request")
        for name in sorted(set(po) & set(pn)):
            if po[name].get("required") is not True and pn[name].get("required") is True:
                out.add("breaking", "parameter_became_required", f"{where} {name[0]}:{name[1]}",
                        f"parameter '{name[1]}' became required", "request")
            so, sn = _schema_of(old, po[name]), _schema_of(new, pn[name])
            if so is None and "type" in po[name]:
                so = po[name]
            if sn is None and "type" in pn[name]:
                sn = pn[name]
            _diff_schema(old, new, so, sn, f"{where} {name[0]}:{name[1]}", "request", out)
        bo, bn = _deref(old, o.get("requestBody")), _deref(new, n.get("requestBody"))
        if isinstance(bn, dict) and bn.get("required") is True and not (isinstance(bo, dict) and bo.get("required") is True):
            out.add("breaking", "request_body_required", where, "request body became required", "request")
        _diff_schema(old, new, _schema_of(old, bo), _schema_of(new, bn), f"{where} request", "request", out)
        ro = o.get("responses") if isinstance(o.get("responses"), dict) else {}
        rn = n.get("responses") if isinstance(n.get("responses"), dict) else {}
        for code in sorted(set(ro) - set(rn), key=str):
            success = str(code).startswith("2")
            out.add("breaking" if success else "non_breaking", "response_removed", f"{where} {code}",
                    f"response {code} removed", "response")
        for code in sorted(set(rn) - set(ro), key=str):
            out.add("non_breaking", "response_added", f"{where} {code}", f"response {code} added", "response")
        for code in sorted(set(ro) & set(rn), key=str):
            _diff_schema(old, new, _schema_of(old, ro[code]), _schema_of(new, rn[code]), f"{where} {code}", "response", out)
    changes = out.changes
    counts = {c: sum(1 for x in changes if x["classification"] == c) for c in ("breaking", "non_breaking", "deprecation")}
    bump = "major" if counts["breaking"] else "minor" if counts["non_breaking"] else "patch"
    return {"compatible": counts["breaking"] == 0, "recommended_semver_bump": bump, "counts": counts,
            "breaking": [c for c in changes if c["classification"] == "breaking"][:100],
            "non_breaking": [c for c in changes if c["classification"] == "non_breaking"][:100],
            "deprecations": [c for c in changes if c["classification"] == "deprecation"][:100],
            "operations": {"old": len(old_ops), "new": len(new_ops)},
            "scope": "request/response schemas are compared to depth %d after resolving local $ref; "
                     "allOf/oneOf/anyOf compositions are not merged" % _MAX_DEPTH}


# --- test generation -----------------------------------------------------------------------

_SAMPLES = {"integer": "1", "number": "1.0", "boolean": "True", "string": "'sample'", "array": "[]", "object": "{}"}


def _py_type(schema: Any) -> str | None:
    return {"string": "str", "integer": "int", "number": "(int, float)", "boolean": "bool",
            "array": "list", "object": "dict"}.get(_type_of(schema))


def generate_tests(spec: dict[str, Any]) -> dict[str, Any]:
    ops = _ops(spec)
    lines = [
        '"""Integration tests generated from the OpenAPI document. Set BASE_URL (and AUTH_HEADER) to run."""',
        "import os", "", "import httpx", "import pytest", "",
        'BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")',
        'HEADERS = {"Authorization": os.environ["AUTH_HEADER"]} if os.environ.get("AUTH_HEADER") else {}',
        "", "",
    ]
    names: set[str] = set()
    skipped: list[str] = []
    for index, ((method, path), op) in enumerate(sorted(ops.items())):
        if index >= _MAX_OPS:
            skipped.append(f"{method.upper()} {path} (more than {_MAX_OPS} operations)")
            continue
        params = _params(spec, op)
        concrete = path
        for (loc, name), p in params.items():
            if loc == "path":
                schema = _schema_of(spec, p) or {}
                example = p.get("example", schema.get("example") if isinstance(schema, dict) else None)
                sample = repr(example) if example is not None else _SAMPLES.get(_type_of(schema), "'1'")
                concrete = concrete.replace("{" + name + "}", "{" + sample.strip("'") + "}")
        query = {}
        for (loc, name), p in params.items():
            if loc == "query" and p.get("required") is True:
                schema = _schema_of(spec, p) or {}
                query[name] = _SAMPLES.get(_type_of(schema), "'1'")
        body_schema = _schema_of(spec, op.get("requestBody"))
        body_expr = None
        if isinstance(body_schema, dict) and _type_of(body_schema) == "object":
            body_props = body_schema.get("properties") if isinstance(body_schema.get("properties"), dict) else {}
            fields = {n: _SAMPLES.get(_type_of(_deref(spec, body_props.get(n))), "'sample'")
                      for n in (body_schema.get("required") or []) if isinstance(n, str)}
            body_expr = "{" + ", ".join(f"{n!r}: {v}" for n, v in fields.items()) + "}"
        responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
        ok_codes = sorted(int(c) for c in responses if str(c).isdigit() and str(c).startswith("2")) or [200]
        name = re.sub(r"\W+", "_", str(op.get("operationId") or f"{method}_{path}")).strip("_").lower()
        while name in names:
            name += "_2"
        names.add(name)
        lines += [f"def test_{name}():",
                  f'    """{method.upper()} {path}: documented statuses {sorted(str(c) for c in responses) or "unspecified"}."""',
                  f"    response = httpx.request({method.upper()!r}, BASE_URL + f{concrete!r}, headers=HEADERS,"
                  + (f" params={{{', '.join(f'{k!r}: {v}' for k, v in query.items())}}}," if query else "")
                  + (f" json={body_expr}," if body_expr else "") + " timeout=15)",
                  f"    documented = {sorted(int(c) for c in responses if str(c).isdigit()) or ok_codes}",
                  "    assert response.status_code in documented, response.text[:300]"]
        ok_schema = None
        for code in ok_codes:
            ok_schema = _schema_of(spec, responses.get(str(code)))
            if ok_schema:
                break
        if isinstance(ok_schema, dict) and _type_of(ok_schema) == "object":
            props = ok_schema.get("properties") if isinstance(ok_schema.get("properties"), dict) else {}
            required = [n for n in ok_schema.get("required") or [] if isinstance(n, str)]
            if required:
                lines += [f"    if response.status_code in {ok_codes}:", "        body = response.json()"]
                for prop in required:
                    lines.append(f"        assert {prop!r} in body")
                    expected = _py_type(_deref(spec, props.get(prop)))
                    if expected:
                        lines.append(f"        assert isinstance(body[{prop!r}], {expected})")
        lines += ["", ""]
    code = "\n".join(lines).rstrip() + "\n"
    return {"test_code": code, "tests": len(names), "skipped": skipped,
            "note": "Text only: nothing was run. Path parameters use examples or placeholders; supply real ids "
                    "and a reachable BASE_URL before running."}
