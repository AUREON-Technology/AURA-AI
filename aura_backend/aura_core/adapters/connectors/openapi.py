"""OpenAPI 3.x connector: fetch a service's API description and map it to artifacts (AP3).

Like ``postgres_catalog``, the mapping (:func:`artifacts_from_openapi`) is pure and replayed by
golden tests; only the fetch talks to the network.

Fetching the spec is an outbound request to an address a Super Admin supplied, so it follows the
same SSRF discipline as the ``fetch_url`` tool (``domain/network.py``):

* the host is resolved first and every address checked; internal addresses are refused unless the
  connector carries ``allow_private_network: true`` -- which is visible in the approval request --
  and even then loopback, link-local (cloud metadata), multicast, reserved and carrier-grade-NAT
  ranges stay refused;
* redirects are never followed; the body is capped at 5 MB; YAML is parsed with ``safe_load``;
* a vaulted credential is only ever sent over https, as a bearer token.

Only internal ``#/components/...`` references are resolved. External references are recorded as
an issue and never fetched -- following them would let the spec author make AURA request any URL.

``write_capable``: AURA itself only ever *reads* an API in v1 (there is no write port). Without a
credential AURA cannot write at all, so health reports ``False``. With a credential the token's
scope is unknown to AURA, so health reports ``None`` (finding ``credential_scope_unknown``) and
activation is refused -- unless the registering Super Admin attests the token is read-only with
the option ``read_only_attested: true`` (AR-3). The option is part of the descriptor, so it is
bound into the registration digest the approver signs; health then reports ``False`` with the
finding ``credential_scope_attested``. The attestation is a recorded human claim, not a probe:
what enforces read-only on AURA's side is unchanged -- every generated template is a ``GET``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from ...domain.connectors import READ_ONLY_ATTESTED, ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import (
    ArtifactKind,
    DiscoveryBudget,
    DiscoveryIssue,
    DiscoveryReport,
    RawArtifact,
    quote_identifier as q,
)
from ...domain.errors import DependencyUnavailableError, ToolExecutionError, ValidationError
from ...domain.templates import ReadTemplate, TemplateKind, render_http
from ...domain.network import is_internal_address
from ...domain.untrusted import MAX_UNTRUSTED_CHARS, UntrustedText
from ...observability.logging import get_logger

__all__ = ["OpenApiConnectorFactory", "OpenApiConnector", "artifacts_from_openapi", "parse_spec", "OPENAPI_EXTRACTOR"]

logger = get_logger("adapters.connectors.openapi")

OPENAPI_EXTRACTOR = "openapi"
MAX_SPEC_BYTES = 5 * 1024 * 1024
#: Largest API response a generated read tool accepts (AP7). The tool's own result cap is lower.
MAX_READ_BYTES = 2 * 1024 * 1024
_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
_SCHEMA_REF = "#/components/schemas/"
#: How many $ref/combinator hops a property type is followed through; also stops ref cycles.
_MAX_TYPE_DEPTH = 8
_PARAM_REF = "#/components/parameters/"
#: What an optional query parameter may be typed as on a generated tool (AR-2). Objects and
#: non-``form`` styles have no unambiguous query-string rendering, so they stay unreachable.
_QUERY_SCALARS = frozenset({"string", "integer", "number", "boolean"})
#: Bounds on what one optional query parameter's descriptor may carry; it is stored as a
#: single <=256-character string so it survives the AUSM scalar-property rule unchanged.
_MAX_QUERY_ENUM = 16
_MAX_QUERY_ENUM_CHARS = 64
_MAX_QUERY_DESCRIPTOR = 256

Resolver = Callable[[str], Awaitable[list[str]]]


async def _system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


def _destination_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> bool:
    if not is_internal_address(address):
        return True
    if not allow_private:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    never = (address.is_loopback or address.is_link_local or address.is_multicast
             or address.is_unspecified or address.is_reserved)
    return address.is_private and not never


# ------------------------------------------------------------------------------------- mapping


def parse_spec(body: bytes) -> dict[str, Any]:
    """JSON or YAML (``safe_load``) to a mapping; refuse anything that is not OpenAPI 3.x."""
    if len(body) > MAX_SPEC_BYTES:
        raise ValidationError("the API description exceeds 5 MB")
    text = body.decode("utf-8", errors="strict") if body else ""
    try:
        spec = json.loads(text) if text.lstrip().startswith("{") else None
        if spec is None:
            import yaml  # lazy

            spec = yaml.safe_load(text)
    except Exception as exc:
        raise ValidationError("the API description is not valid JSON or YAML") from exc
    if not isinstance(spec, dict):
        raise ValidationError("the API description is not a mapping")
    version = str(spec.get("openapi", ""))
    if not version.startswith("3."):
        raise ValidationError("only OpenAPI 3.x descriptions are supported", details={"found": version or str(spec.get("swagger", ""))[:16]})
    return spec


def _text(value: Any, limit: int = MAX_UNTRUSTED_CHARS) -> UntrustedText:
    return UntrustedText.of(str(value)[: limit * 2], max_chars=limit)


def _optional_text(value: Any) -> UntrustedText | None:
    return _text(value) if isinstance(value, str) and value.strip() else None


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:512]


class _Mapper:
    def __init__(self, source_id: str, spec: Mapping[str, Any], budget: DiscoveryBudget) -> None:
        self.source_id = source_id
        self.spec = spec
        self.budget = budget
        self.artifacts: list[RawArtifact] = []
        self.issues: list[DiscoveryIssue] = []
        components = spec.get("components") if isinstance(spec.get("components"), dict) else {}
        self.schemas = components.get("schemas") if isinstance(components.get("schemas"), dict) else {}
        self.parameters = components.get("parameters") if isinstance(components.get("parameters"), dict) else {}
        self.security = components.get("securitySchemes") if isinstance(components.get("securitySchemes"), dict) else {}

    def ref(self, node: Any, owner: str) -> str | None:
        """A schema locator for ``{"$ref": "#/components/schemas/X"}``; issues for anything else."""
        if not isinstance(node, dict) or "$ref" not in node:
            return None
        target = str(node["$ref"])
        if target.startswith(_SCHEMA_REF):
            return f"schema:{q(target[len(_SCHEMA_REF):])}"
        self.issues.append(DiscoveryIssue("external_ref_ignored" if not target.startswith("#") else "unsupported_ref", owner))
        return None

    def json_type(self, node: Any, depth: int = 0) -> tuple[str | None, bool]:
        """(JSON type, nullable) that values of ``node`` take on the wire; (None, False) if unknown.

        A property's own ``type`` is often absent: ``{"$ref": ...}``, ``allOf: [{"$ref": ...}]``,
        ``anyOf: [X, {"type": "null"}]`` and OpenAPI 3.1's ``type: [X, "null"]`` all describe a
        typed value without saying so directly. Recording None for them made the compiler type
        every nested object as a string, so a tool rejected each real response (ADR-025). Pure:
        refs are resolved against ``components/schemas`` only and record no issues (``ref``
        does that, once per property).
        """
        if not isinstance(node, dict) or depth > _MAX_TYPE_DEPTH:
            return None, False
        nullable = node.get("nullable") is True
        declared = node.get("type")
        if isinstance(declared, str):
            return declared, nullable or declared == "null"
        if isinstance(declared, list):
            concrete = {t for t in declared if isinstance(t, str) and t != "null"}
            return (concrete.pop() if len(concrete) == 1 else None), nullable or "null" in declared
        target = node.get("$ref")
        if isinstance(target, str):
            if not target.startswith(_SCHEMA_REF):
                return None, nullable
            resolved, resolved_nullable = self.json_type(self.schemas.get(target[len(_SCHEMA_REF):]), depth + 1)
            return resolved, nullable or resolved_nullable
        for combinator in ("allOf", "anyOf", "oneOf"):
            members = node.get(combinator)
            if not isinstance(members, list) or not members:
                continue
            types, member_nullable = set(), False
            for member in members:
                member_type, is_nullable = self.json_type(member, depth + 1)
                member_nullable = member_nullable or is_nullable
                if member_type is None:
                    return None, nullable  # one unknown member makes the union unknown
                if member_type != "null":
                    types.add(member_type)
            # allOf intersects, so one member's null does not make the whole nullable.
            nullable = nullable or (member_nullable and combinator != "allOf")
            return (types.pop() if len(types) == 1 else None), nullable
        if isinstance(node.get("properties"), dict):
            return "object", nullable
        if isinstance(node.get("items"), dict):
            return "array", nullable
        return None, nullable

    def single_ref(self, prop: Mapping[str, Any], owner: str) -> str | None:
        """The schema a property points at: directly, or as the one non-null member of a union."""
        if "$ref" in prop:
            return self.ref(prop, owner)
        for combinator in ("allOf", "anyOf", "oneOf"):
            members = prop.get(combinator)
            if isinstance(members, list):
                concrete = [m for m in members if not (isinstance(m, dict) and m.get("type") == "null")]
                if len(concrete) == 1 and isinstance(concrete[0], dict) and "$ref" in concrete[0]:
                    return self.ref(concrete[0], owner)
        return None

    def schema_target(self, schema: Any, owner: str) -> tuple[str | None, bool]:
        """(schema locator or 'inline', is array) for a request/response schema."""
        if not isinstance(schema, dict):
            return None, False
        if schema.get("type") == "array":
            items = schema.get("items")
            return (self.ref(items, owner) or ("inline" if isinstance(items, dict) else None)), True
        return (self.ref(schema, owner) or "inline"), False

    def run(self) -> DiscoveryReport:
        info = self.spec.get("info") if isinstance(self.spec.get("info"), dict) else {}
        servers = tuple(str(s.get("url"))[:512] for s in self.spec.get("servers", []) if isinstance(s, dict) and s.get("url"))
        self.artifacts.append(RawArtifact(
            kind=ArtifactKind.API, locator="api:spec", name=_text(info.get("title") or "API", 200),
            extractor=OPENAPI_EXTRACTOR, description=_optional_text(info.get("description")),
            attributes={"openapi": str(self.spec.get("openapi"))[:32], "api_version": _scalar(info.get("version")),
                        "servers": servers[:32]},
        ))
        for name, scheme in sorted(self.security.items()):
            if not isinstance(scheme, dict):
                continue
            flows = scheme.get("flows") if isinstance(scheme.get("flows"), dict) else {}
            self.artifacts.append(RawArtifact(
                kind=ArtifactKind.SECURITY_SCHEME, locator=f"security:{q(str(name))}", name=_text(name, 200),
                extractor=OPENAPI_EXTRACTOR, description=_optional_text(scheme.get("description")),
                attributes={"type": _scalar(scheme.get("type")), "scheme": _scalar(scheme.get("scheme")),
                            "in": _scalar(scheme.get("in")), "flows": tuple(sorted(str(f) for f in flows))},
            ))
        for name, schema in sorted(self.schemas.items()):
            self.add_schema(str(name), schema)
        paths = self.spec.get("paths") if isinstance(self.spec.get("paths"), dict) else {}
        for path, item in sorted(paths.items()):
            if isinstance(item, dict):
                self.add_path(str(path), item)
        truncated = False
        if len(self.artifacts) > self.budget.max_objects:
            self.artifacts = sorted(self.artifacts, key=lambda a: a.locator)[: self.budget.max_objects]
            truncated = True
            self.issues.append(DiscoveryIssue("budget_exhausted", "artifacts"))
        return DiscoveryReport.build(self.source_id, OPENAPI_EXTRACTOR, self.artifacts, issues=self.issues, truncated=truncated)

    def add_schema(self, name: str, schema: Any) -> None:
        if not isinstance(schema, dict):
            return
        locator = f"schema:{q(name)}"
        required = tuple(str(r) for r in schema.get("required", []) if isinstance(r, str))
        self.artifacts.append(RawArtifact(
            kind=ArtifactKind.SCHEMA_OBJECT, locator=locator, name=_text(name, 200), extractor=OPENAPI_EXTRACTOR,
            description=_optional_text(schema.get("description")),
            attributes={"type": _scalar(schema.get("type", "object")), "required": required[:256]},
        ))
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for prop_name, prop in sorted(props.items()):
            if not isinstance(prop, dict):
                continue
            prop_locator = f"prop:{q(name)}.{q(str(prop_name))}"
            items = prop.get("items") if isinstance(prop.get("items"), dict) else None
            enum = prop.get("enum") if isinstance(prop.get("enum"), list) else None
            json_type, nullable = self.json_type(prop)
            self.artifacts.append(RawArtifact(
                kind=ArtifactKind.SCHEMA_PROPERTY, locator=prop_locator, name=_text(prop_name, 200),
                extractor=OPENAPI_EXTRACTOR, description=_optional_text(prop.get("description")), parent=locator,
                attributes={
                    "type": _scalar(json_type), "format": _scalar(prop.get("format")),
                    "ref": self.single_ref(prop, prop_locator),
                    "items_ref": self.ref(items, prop_locator) if items else None,
                    "enum": tuple(_scalar(v) for v in enum[:256]) if enum else None,
                    "nullable": nullable, "read_only": bool(prop.get("readOnly", False)),
                    "required": str(prop_name) in required,
                },
            ))

    def add_path(self, path: str, item: Mapping[str, Any]) -> None:
        shared = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            locator = f"op:{method.upper()} {path}"
            params = [self.param(p, locator) for p in [*shared, *(op.get("parameters") or [])]]
            params = [p for p in params if p is not None]
            request_schema = None
            body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else None
            if body:
                content = body.get("content") if isinstance(body.get("content"), dict) else {}
                json_body = content.get("application/json") if isinstance(content.get("application/json"), dict) else None
                request_schema, _ = self.schema_target(json_body.get("schema") if json_body else None, locator)
            response_schema, response_is_array = None, False
            raw = op.get("responses") if isinstance(op.get("responses"), dict) else {}
            # YAML reads `200:` as an int and JSON as a string; normalise once.
            responses = {str(code): resp for code, resp in raw.items()}
            for code in sorted(responses):
                resp = responses[code]
                if code.startswith("2") and isinstance(resp, dict):
                    content = resp.get("content") if isinstance(resp.get("content"), dict) else {}
                    json_resp = content.get("application/json") if isinstance(content.get("application/json"), dict) else None
                    if json_resp:
                        response_schema, response_is_array = self.schema_target(json_resp.get("schema"), locator)
                    break
            security = op.get("security", self.spec.get("security", []))
            schemes = sorted({str(k) for req in security if isinstance(req, dict) for k in req}) if isinstance(security, list) else []
            operation_id = op.get("operationId")
            self.artifacts.append(RawArtifact(
                kind=ArtifactKind.OPERATION, locator=locator,
                name=_text(operation_id or f"{method.upper()} {path}", 200), extractor=OPENAPI_EXTRACTOR,
                description=_optional_text(op.get("summary") or op.get("description")),
                attributes={
                    "method": method.upper(), "path": path[:512], "operation_id": _scalar(operation_id),
                    "tags": tuple(str(t)[:128] for t in op.get("tags", []) if isinstance(t, str))[:32],
                    "parameters": tuple(f"{p['in']}:{p['name']}" for p in params)[:128],
                    "required_parameters": tuple(f"{p['in']}:{p['name']}" for p in params if p["required"])[:128],
                    "query_parameters": tuple(
                        p["query"] for p in params
                        if p["in"] == "query" and not p["required"] and p.get("query"))[:128],
                    "request_schema": request_schema, "response_schema": response_schema,
                    "response_is_array": response_is_array, "security": tuple(schemes)[:32],
                    "deprecated": bool(op.get("deprecated", False)),
                },
            ))

    def param(self, node: Any, owner: str) -> dict[str, Any] | None:
        if isinstance(node, dict) and "$ref" in node:
            target = str(node["$ref"])
            if not target.startswith(_PARAM_REF):
                self.issues.append(DiscoveryIssue("external_ref_ignored" if not target.startswith("#") else "unsupported_ref", owner))
                return None
            node = self.parameters.get(target[len(_PARAM_REF):])
        if not isinstance(node, dict) or not node.get("name") or not node.get("in"):
            return None
        where = str(node["in"])
        param = {"name": str(node["name"])[:128], "in": where[:16], "required": bool(node.get("required", where == "path"))}
        if where == "query" and not param["required"]:
            param["query"] = self.query_descriptor(node, owner)
        return param

    def query_descriptor(self, node: Mapping[str, Any], owner: str) -> str | None:
        """An optional query parameter as compact JSON: name, type, and the checkable constraints.

        ``{"n": name, "t": type, "i": item type, "e": enum, "lo"/"hi": bounds, "d": default}``
        -- only what a generated tool can enforce or a caller can rely on (AR-2). The enum and
        default are the connected system's text; the compiler decides what of it is safe to show
        a model. None (with an issue) when the parameter cannot be rendered into a query string.
        """
        schema = node.get("schema") if isinstance(node.get("schema"), dict) else {}
        target = schema.get("$ref")
        if isinstance(target, str) and target.startswith(_SCHEMA_REF):
            schema = self.schemas.get(target[len(_SCHEMA_REF):]) or {}
        kind, _ = self.json_type(schema)
        item_kind = None
        if kind == "array":
            explode = node.get("explode", True)
            if node.get("style", "form") != "form" or explode is not True:
                self.issues.append(DiscoveryIssue("unsupported_query_parameter", owner))
                return None
            item_kind, _ = self.json_type(schema.get("items"))
            if item_kind not in _QUERY_SCALARS:
                self.issues.append(DiscoveryIssue("unsupported_query_parameter", owner))
                return None
        elif kind not in _QUERY_SCALARS:
            self.issues.append(DiscoveryIssue("unsupported_query_parameter", owner))
            return None
        out: dict[str, Any] = {"n": str(node["name"])[:128], "t": kind}
        if item_kind:
            out["i"] = item_kind
        values = schema.get("enum")
        if kind == "string" and isinstance(values, list) and 0 < len(values) <= _MAX_QUERY_ENUM and all(
            isinstance(v, str) and 0 < len(v) <= _MAX_QUERY_ENUM_CHARS for v in values
        ):
            out["e"] = values
        for key, short in (("minimum", "lo"), ("maximum", "hi")):
            bound = schema.get(key)
            if kind in ("integer", "number") and isinstance(bound, (int, float)) and not isinstance(bound, bool):
                out[short] = bound
        default = schema.get("default")
        if isinstance(default, (str, int, float, bool)) and (not isinstance(default, str) or len(default) <= 64):
            out["d"] = default
        encoded = json.dumps(out, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
        if len(encoded) > _MAX_QUERY_DESCRIPTOR and "e" in out:
            del out["e"]  # an enum too long to carry is dropped, never truncated into a wrong one
            encoded = json.dumps(out, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
        if len(encoded) > _MAX_QUERY_DESCRIPTOR:
            self.issues.append(DiscoveryIssue("query_parameter_too_large", owner))
            return None
        return encoded


def artifacts_from_openapi(source_id: str, spec: Mapping[str, Any], budget: DiscoveryBudget) -> DiscoveryReport:
    """The deterministic mapping from a parsed OpenAPI 3.x description to raw artifacts."""
    return _Mapper(source_id, spec, budget).run()


# ----------------------------------------------------------------------------------- connector


class OpenApiConnectorFactory:
    def __init__(self, *, transport: Any = None, resolver: Resolver | None = None) -> None:
        self._transport = transport
        self._resolver = resolver or _system_resolver

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "OpenApiConnector":
        return OpenApiConnector(descriptor, credential, transport=self._transport, resolver=self._resolver)


class OpenApiConnector:
    def __init__(self, descriptor: ConnectorDescriptor, credential: Credential | None, *,
                 transport: Any = None, resolver: Resolver = _system_resolver) -> None:
        self.descriptor = descriptor
        self._credential = credential
        self._transport = transport
        self._resolver = resolver

    async def _fetch(self) -> tuple[bytes | None, str]:
        """(body, "ok") or (None, reason code)."""
        return await self._get(self.descriptor.endpoint, limit=MAX_SPEC_BYTES, too_large="spec_too_large")

    async def _get(self, url: str, *, limit: int, too_large: str) -> tuple[bytes | None, str]:
        """GET ``url`` under the SSRF policy: (body, "ok") or (None, reason code)."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None, "invalid_endpoint"
        if self._credential is not None and parts.scheme != "https":
            return None, "credential_requires_https"
        allow_private = self.descriptor.options.get("allow_private_network") is True
        try:
            literal = ipaddress.ip_address(parts.hostname)
            addresses = [str(literal)]
        except ValueError:
            try:
                addresses = await self._resolver(parts.hostname)
            except (OSError, socket.gaierror):
                return None, "unreachable"
        if not addresses:
            return None, "unreachable"
        for address in addresses:
            if not _destination_allowed(ipaddress.ip_address(address), allow_private=allow_private):
                return None, "destination_refused"
        import httpx  # lazy

        headers = {"Accept": "application/json, application/yaml;q=0.9, */*;q=0.1", "User-Agent": "AURA/3.0"}
        if self._credential is not None:
            headers["Authorization"] = f"Bearer {self._credential.reveal()}"
        try:
            async with httpx.AsyncClient(timeout=self.descriptor.timeout_s, follow_redirects=False,
                                         transport=self._transport) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        return None, "redirect_refused"
                    if response.status_code in (401, 403):
                        return None, "auth_failed"
                    if response.status_code >= 400:
                        return None, "http_error"
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            return None, too_large
        except httpx.TimeoutException:
            return None, "timeout"
        except httpx.HTTPError:
            return None, "unreachable"
        return bytes(body), "ok"

    async def health(self) -> ConnectorHealth:
        started = time.monotonic()
        body, reason = await self._fetch()
        latency = round((time.monotonic() - started) * 1000.0, 1)
        if body is None:
            return ConnectorHealth(HealthStatus.UNHEALTHY, reason, latency_ms=latency)
        try:
            parse_spec(body)
        except ValidationError:
            return ConnectorHealth(HealthStatus.UNHEALTHY, "invalid_spec", latency_ms=latency)
        if self._credential is not None:
            if self.descriptor.options.get(READ_ONLY_ATTESTED) is True:
                return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=False, latency_ms=latency,
                                       findings=("credential_scope_attested",))
            return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=None, latency_ms=latency,
                                   findings=("credential_scope_unknown",))
        return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=False, latency_ms=latency)

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        body, reason = await self._fetch()
        if body is None:
            raise DependencyUnavailableError(
                "the API description could not be fetched", dependency="connector",
                details={"connector": self.descriptor.id, "reason": reason},
            )
        return artifacts_from_openapi(self.descriptor.id, parse_spec(body), budget)

    async def read(self, template: ReadTemplate, arguments: Mapping[str, Any]) -> Any:
        """``GET`` one compiled template against the approved ``base_url`` (AP7).

        The base URL is a descriptor option, so it is part of the digest the registration
        approval bound; the path comes from the template (twin-allowlisted), each argument is
        percent-encoded into its own segment, and the request passes the same SSRF policy as the
        spec fetch. A delegated connector forwards no user token yet (AP10): it reads what the
        API serves anonymously.
        """
        if template.kind is not TemplateKind.HTTP or template.connector != self.descriptor.id:
            raise ToolExecutionError("this template does not belong to this connector", tool=template.id)
        base = self.descriptor.options.get("base_url")
        if not isinstance(base, str) or urlsplit(base).scheme not in ("http", "https") or "?" in base or "#" in base:
            raise ToolExecutionError("this API connector has no base_url configured for reads", tool=template.id)
        body, reason = await self._get(base.rstrip("/") + render_http(template, arguments),
                                       limit=MAX_READ_BYTES, too_large="response_too_large")
        if body is None:
            raise ToolExecutionError("the API read failed", tool=template.id, details={"reason": reason})
        try:
            return json.loads(body)
        except ValueError:
            raise ToolExecutionError("the API did not return JSON", tool=template.id) from None

    async def aclose(self) -> None:
        return None
