"""Git connector (AP10): what a repository's code says about the system it implements.

A ``git`` connector's endpoint is a checkout directory, relative to the operator-configured root
(``AURA_ADAPTATION_GIT_ROOT``) -- a read-only clone the operator keeps current, the same
containment the Files connector uses. With no root configured the kind is not offered. AURA never
runs ``git`` here and never executes a line of the repository: files are read as bytes and parsed.

What discovery reads, in this order, into one :class:`~.code_schema.SchemaModel`:

1. **SQL** (``*.sql``) in natural path order -- ``schema.sql`` and numbered migrations
   (``V2__x.sql`` before ``V10__y.sql``);
2. **Alembic migrations** (``*/versions/*.py`` with an ``upgrade()``) in ``down_revision`` order;
3. **ORM models** -- SQLAlchemy and Django -- for tables no migration declared.

The model's snapshot goes through ``postgres_catalog.artifacts_from_catalog`` exactly like a live
catalog, each relation stamped with where it was declared (``declared_in``, ``file``). Then:

4. **API descriptions** -- the first ``openapi``/``swagger`` file (3.x) -- through the OpenAPI
   connector's own mapping;
5. **Routes** -- FastAPI and Flask handlers -- as operations, unless the description already has
   that method and path;
6. **Documentation** -- a root ``README`` and anything under a top-level ``docs/`` or ``doc/``
   directory in Markdown, plain text, HTML or PDF -- as DOCUMENT artifacts (``document:<path>``),
   which a blueprint can make project knowledge and the nightly refresh can read through
   :meth:`GitConnector.list_documents` / :meth:`GitConnector.fetch_document`.

Every artifact cites a ``code_*`` extractor, so the twin holds it below full confidence and marks
the source ``queryable: false``: code is evidence about the live system and a candidate to match
against it (``same_as``), never a place tools read from (``adaptation/normalize.py``,
``adaptation/planner.py``). Code text is untrusted (ADR-022): names and docstrings arrive as
``UntrustedText`` and nothing is evaluated.

GitHub-hosted repositories read over the API are not offered yet: activation requires proof that
the credential cannot write (``ConnectorHealth.write_capable is False``), and a GitHub token's
write capability cannot be verified from the token itself. A mounted read-only checkout carries no
credential at all.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from ...domain.connectors import ConnectorDescriptor, ConnectorHealth, Credential, HealthStatus
from ...domain.discovery import (
    ArtifactKind,
    DiscoveryBudget,
    DiscoveryIssue,
    DiscoveryReport,
    RawArtifact,
    SourceDocument,
)
from ...domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from ...domain.untrusted import UntrustedText
from ...observability.logging import get_logger
from .code_python import alembic_order, read_alembic, read_python_models, resolve_foreign_keys, routes
from .code_schema import SchemaModel, read_sql
from .files import DOCUMENT_SUFFIXES, document_name_ok, read_capped, resolve_directory
from .postgres_catalog import artifacts_from_catalog

__all__ = ["GitConnectorFactory", "GitConnector", "scan_repository", "GIT_EXTRACTOR"]

logger = get_logger("adapters.connectors.git")

GIT_EXTRACTOR = "git"
SCHEMA_EXTRACTOR = "code_schema"
OPENAPI_EXTRACTOR = "code_openapi"
ROUTES_EXTRACTOR = "code_routes"
DOCUMENTS_EXTRACTOR = "code_documents"
#: Top-level directories whose documents are the repository's documentation.
_DOC_DIRS = frozenset({"docs", "doc"})

#: Directories never read: VCS metadata, dependencies, build output, and test code (whose models
#: and routes are fixtures, not the application).
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "site-packages", "dist", "build", "vendor", "third_party", "tests", "test", "testing",
})
_SPEC_NAMES = frozenset({"openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json"})
#: One source file larger than this is skipped with an issue; code this size is generated, not schema.
MAX_FILE_BYTES = 2 * 1024 * 1024
#: Bytes read from one repository; past it the scan stops and says so.
MAX_TOTAL_BYTES = 64 * 1024 * 1024
#: Files examined (listed, not necessarily read) from one repository.
MAX_FILES = 20_000

_MODEL_MARKERS = ("__tablename__", "models.Model", "Model)")
_ROUTE_MARKER = re.compile(r"@\s*\w+\s*\.\s*(get|post|put|patch|delete|head|options|route|api_route)\s*\(")
_ALEMBIC_MARKERS = ("def upgrade", "op.")


def _natural(path: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]


def _issue(code: str, locator: str) -> dict[str, str]:
    return {"code": code, "locator": locator[:512]}


@dataclasses.dataclass
class _Scan:
    sql: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    alembic: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    python: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    specs: list[tuple[str, bytes]] = dataclasses.field(default_factory=list)
    documents: list[tuple[str, int, str]] = dataclasses.field(default_factory=list)  # path, bytes, sha256
    issues: list[dict[str, str]] = dataclasses.field(default_factory=list)
    truncated: bool = False


def _walk(root: Path) -> _Scan:
    """Read the candidate files of a checkout, without following a symlink anywhere."""
    scan = _Scan()
    examined = total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")
                         and not os.path.islink(os.path.join(current, d)))
        for name in sorted(files):
            examined += 1
            if examined > MAX_FILES:
                scan.truncated = True
                scan.issues.append(_issue("file_budget_exhausted", "file:"))
                return scan
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            lower = name.lower()
            is_sql, is_py, is_spec = lower.endswith(".sql"), lower.endswith(".py"), lower in _SPEC_NAMES
            is_doc = documentation_path(rel)
            if not (is_sql or is_py or is_spec or is_doc):
                continue
            if path.is_symlink():
                scan.issues.append(_issue("symlink_skipped", f"file:{rel}"))
                continue
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    scan.issues.append(_issue("file_too_large", f"file:{rel}"))
                    continue
                if total + size > MAX_TOTAL_BYTES:
                    scan.truncated = True
                    scan.issues.append(_issue("byte_budget_exhausted", f"file:{rel}"))
                    return scan
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
                total += len(data)
            except OSError:
                scan.issues.append(_issue("unreadable", f"file:{rel}"))
                continue
            if is_doc:
                scan.documents.append((rel, len(data), hashlib.sha256(data).hexdigest()))
                continue
            if is_spec:
                scan.specs.append((rel, data))
                continue
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                scan.issues.append(_issue("unreadable", f"file:{rel}"))
                continue
            if is_sql:
                scan.sql.append((rel, text))
            elif "/versions/" in f"/{rel}" and all(m in text for m in _ALEMBIC_MARKERS):
                scan.alembic.append((rel, text))
            elif any(m in text for m in _MODEL_MARKERS) or _ROUTE_MARKER.search(text):
                scan.python.append((rel, text))
    return scan


def documentation_path(rel: str) -> bool:
    """A root ``README.*`` or a file under ``docs/``/``doc/``, in a document format, named plainly.

    Every segment must pass the Files connector's name check (no dot-files, no control or
    direction-changing characters), which is also what keeps ``..`` out of a locator.
    """
    parts = rel.split("/")
    if not parts or "\\" in rel or not all(document_name_ok(part) for part in parts):
        return False
    if Path(parts[-1]).suffix.lower() not in DOCUMENT_SUFFIXES:
        return False
    if len(parts) == 1:
        return parts[0].lower().startswith("readme")
    return parts[0] in _DOC_DIRS


def _document_artifact(rel: str, size: int, sha256: str) -> RawArtifact:
    return RawArtifact(
        ArtifactKind.DOCUMENT, f"document:{rel}"[:512], UntrustedText(rel[:200]), DOCUMENTS_EXTRACTOR,
        attributes={"format": DOCUMENT_SUFFIXES[Path(rel).suffix.lower()], "bytes": size, "sha256": sha256,
                    "file": rel[:512]},
    )


def _app_label(rel: str) -> str:
    """Django's default app label: the package holding ``models.py`` (or the ``models/`` package)."""
    parts = rel.split("/")
    if len(parts) >= 3 and parts[-2] == "models":
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else "app"


def _stamp(artifact: RawArtifact, model: SchemaModel) -> RawArtifact:
    """A relation artifact with where it was declared; everything else unchanged."""
    if artifact.kind not in (ArtifactKind.TABLE, ArtifactKind.VIEW):
        return artifact
    schema = str(artifact.attributes.get("schema") or "")
    origin = model.origin_of(schema, artifact.name.raw())
    if origin is None:
        return artifact
    declared_in, path = origin
    return dataclasses.replace(artifact, attributes={**artifact.attributes, "declared_in": declared_in,
                                                     "file": path[:512]})


def _route_artifact(route: Any, rel: str) -> RawArtifact:
    params = tuple(f"path:{p}" for p in route.path_parameters)
    return RawArtifact(
        kind=ArtifactKind.OPERATION, locator=f"op:{route.method} {route.path}"[:512],
        name=UntrustedText.of(route.handler, max_chars=200), extractor=ROUTES_EXTRACTOR,
        description=UntrustedText.of(route.summary, max_chars=300) if route.summary else None,
        attributes={
            "method": route.method, "path": route.path[:512], "operation_id": route.handler[:128],
            "parameters": params, "required_parameters": params, "declared_in": "route", "file": rel[:512],
        },
    )


def scan_repository(source_id: str, root: Path, budget: DiscoveryBudget) -> DiscoveryReport:
    """Discover one checkout. Pure apart from reading files under ``root``."""
    scan = _walk(root)
    model = SchemaModel()
    for rel, text in sorted(scan.sql, key=lambda item: _natural(item[0])):
        read_sql(model, text, path=rel, origin="migration" if "migration" in rel.lower() else "ddl")
    pending: list[Any] = []
    for rel, text in alembic_order(scan.alembic):
        read_alembic(model, text, path=rel, pending=pending)
    for rel, text in scan.python:
        if any(m in text for m in _MODEL_MARKERS):
            pending.extend(read_python_models(model, text, path=rel, app_label=_app_label(rel)))
    resolve_foreign_keys(model, pending)

    report = artifacts_from_catalog(source_id, model.snapshot(extra_issues=scan.issues), budget,
                                    extractor=GIT_EXTRACTOR, catalog_extractor=SCHEMA_EXTRACTOR,
                                    profile_extractor=SCHEMA_EXTRACTOR)
    artifacts = [_stamp(a, model) for a in report.artifacts]
    issues = list(report.issues)
    truncated = report.truncated or scan.truncated

    if scan.specs:
        from .openapi import artifacts_from_openapi, parse_spec

        (spec_path, body), *others = scan.specs
        issues.extend(DiscoveryIssue("additional_spec_skipped", f"file:{p}"[:512]) for p, _ in others)
        try:
            spec_report = artifacts_from_openapi(source_id, parse_spec(body), budget)
        except ValidationError:
            issues.append(DiscoveryIssue("spec_unparseable", f"file:{spec_path}"[:512]))
        else:
            taken = {a.locator for a in artifacts}
            artifacts.extend(dataclasses.replace(a, extractor=OPENAPI_EXTRACTOR) for a in spec_report.artifacts
                             if a.locator not in taken)
            issues.extend(spec_report.issues)
            truncated = truncated or spec_report.truncated

    taken = {a.locator for a in artifacts}
    for rel, size, sha256 in scan.documents:
        artifact = _document_artifact(rel, size, sha256)
        if artifact.locator not in taken:
            taken.add(artifact.locator)
            artifacts.append(artifact)
    for rel, text in scan.python:
        for route in routes(text, path=rel):
            artifact = _route_artifact(route, rel)
            if artifact.locator not in taken:
                taken.add(artifact.locator)
                artifacts.append(artifact)

    if len(artifacts) > budget.max_objects:
        artifacts = sorted(artifacts, key=lambda a: a.locator)[: budget.max_objects]
        truncated = True
        issues.append(DiscoveryIssue("budget_exhausted", "artifacts"))
    return DiscoveryReport.build(source_id, GIT_EXTRACTOR, artifacts, issues=issues, truncated=truncated)


class GitConnectorFactory:
    def __init__(self, *, root: str | None) -> None:
        self._root = root or None

    @property
    def available(self) -> bool:
        return self._root is not None

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> "GitConnector":
        return GitConnector(descriptor, root=self._root)


class GitConnector:
    """A read-only checkout. Discovery only: there is nothing in a repository a tool could read."""

    def __init__(self, descriptor: ConnectorDescriptor, *, root: str | None) -> None:
        self.descriptor = descriptor
        self._root = root

    def _directory(self) -> tuple[Path | None, str]:
        if not self._root:
            return None, "git_disabled"
        directory = resolve_directory(self._root, self.descriptor.endpoint)
        return (directory, "ok") if directory is not None else (None, "invalid_endpoint")

    async def health(self) -> ConnectorHealth:
        started = time.perf_counter()
        directory, reason = self._directory()
        latency = (time.perf_counter() - started) * 1000.0
        if directory is None:
            return ConnectorHealth(HealthStatus.UNHEALTHY, reason, latency_ms=latency)
        if not os.access(directory, os.R_OK | os.X_OK):
            return ConnectorHealth(HealthStatus.UNHEALTHY, "not_readable", latency_ms=latency)
        # No credential is held, and nothing here writes: files are opened read-only.
        return ConnectorHealth(HealthStatus.HEALTHY, "ok", write_capable=False, latency_ms=latency)

    def _require_directory(self) -> Path:
        directory, reason = self._directory()
        if directory is None:
            raise DependencyUnavailableError(
                "the repository checkout is not available", dependency="connector",
                details={"connector": self.descriptor.id, "reason": reason},
            )
        return directory

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport:
        return scan_repository(self.descriptor.id, self._require_directory(), budget)

    async def list_documents(self) -> list[str]:
        """The checkout's documentation, as ``document:<path>`` locators, without reading it."""
        root = self._require_directory()
        found: list[str] = []
        candidates = [p for p in sorted(root.iterdir()) if p.name.lower().startswith("readme")]
        for top in sorted(_DOC_DIRS):
            base = root / top
            if base.is_dir() and not base.is_symlink():
                for current, dirs, files in os.walk(base, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(current, d)))
                    candidates.extend(Path(current) / name for name in sorted(files))
        for path in candidates:
            rel = path.relative_to(root).as_posix()
            if path.is_file() and not path.is_symlink() and documentation_path(rel):
                found.append(f"document:{rel}")
            if len(found) >= MAX_FILES:
                break
        return found

    async def fetch_document(self, locator: str) -> SourceDocument:
        """One documentation file's bytes. Only a path :meth:`list_documents` could have named."""
        rel = locator.removeprefix("document:")
        if not locator.startswith("document:") or not documentation_path(rel):
            raise ValidationError("not a documentation locator of this checkout", details={"locator": locator[:80]})
        root = self._require_directory()
        path = root
        for part in rel.split("/"):
            path = path / part
            if path.is_symlink():
                raise NotFoundError("the document is not in the checkout",
                                    details={"connector": self.descriptor.id, "locator": locator[:200]})
        if not path.is_file():
            raise NotFoundError("the document is not in the checkout",
                                details={"connector": self.descriptor.id, "locator": locator[:200]})
        try:
            data = read_capped(path)
        except (OSError, ValueError):
            raise NotFoundError("the document could not be read",
                                details={"connector": self.descriptor.id, "locator": locator[:200]}) from None
        return SourceDocument(rel, DOCUMENT_SUFFIXES[path.suffix.lower()], data, hashlib.sha256(data).hexdigest())

    async def aclose(self) -> None:
        return None
