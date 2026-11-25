"""Operator HTTP surface for kernel services already on the container.

Kept out of ``v3.py`` so the gateway adapter does not become a monolith.
Every mutating handler writes an audit event. SuperAdmin-only routes are
marked with ``require_super_admin``.
"""

import asyncio
from typing import Any, Callable, Literal, Sequence

from ..domain.errors import (
    AuthorizationError,
    ConflictError,
    KnowledgeError,
    NotFoundError,
    ValidationError,
)
from ..domain.knowledge import (
    DEFAULT_ALLOWED_LICENSES,
    IndexIdentity,
    KnowledgeSource,
    LicenseStatus,
    SourceType,
    TrustTier,
)
from ..knowledge.source_retirement import documents_of_source, retire_source_documents
from ..learning.approval import ApprovalKind
from ..learning.refresh_runs import RefreshRunTracker
from ..mlops.model_registry import ModelStage
from ..observability.logging import get_logger

logger = get_logger("api.v3.admin")

#: Approval kinds only a Super Admin may decide (approve or reject).
_SUPER_ADMIN_APPROVAL_KINDS = frozenset(
    {ApprovalKind.CONNECTOR_REGISTRATION, ApprovalKind.BLUEPRINT_DEPLOYMENT}
)


def _source_payload(
    source: KnowledgeSource, allowed_licenses: Sequence[str] = DEFAULT_ALLOWED_LICENSES
) -> dict[str, Any]:
    return {
        "id": source.id,
        "type": str(source.type),
        "uri": source.uri,
        "enabled": source.enabled,
        "trust_tier": int(source.trust_tier),
        "schedule_cron": source.schedule_cron,
        "max_documents": source.max_documents,
        "max_bytes_per_document": source.max_bytes_per_document,
        "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
        "last_cursor": source.last_cursor,
        "owner": source.owner_id.value if source.owner_id else None,
        "name": source.name,
        "publisher": source.publisher,
        "domains": list(source.domains),
        "language": source.language,
        "license_spdx": source.license_spdx,
        "license_status": str(source.license_status),
        "attribution": source.attribution,
        # False means the nightly refresh will skip it: a global source needs an approved licence
        # on the allow-list (D-001). Shown so an operator does not have to infer it.
        "ingestible": source.permits_global_corpus(allowed_licenses),
    }


def _checked_licence(spdx: str | None, allowed: Sequence[str]) -> LicenseStatus:
    """The status a stated licence earns: approved if it is on the allow-list, else a refusal.

    Stating the licence is the Super Admin's attestation that they read the source's terms.
    Nothing stated leaves the status unknown, which keeps the source registered but inert.
    """
    if not spdx or not spdx.strip():
        return LicenseStatus.UNKNOWN
    if spdx.strip().lower() not in {a.strip().lower() for a in allowed}:
        raise ValidationError(
            "this licence is not accepted for the global corpus",
            details={"license_spdx": spdx, "allowed": list(allowed)},
        )
    return LicenseStatus.APPROVED


def _clean_domains(domains: Sequence[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(d.strip().lower() for d in domains if d and d.strip()))
    if any(len(d) > 64 for d in cleaned):
        raise ValidationError("a domain is at most 64 characters")
    return cleaned


def _parse_index_name(physical_name: str, fallback: IndexIdentity) -> IndexIdentity:
    parts = physical_name.rsplit("__", 3)
    if len(parts) != 4 or not parts[1].startswith("v"):
        raise ValidationError(
            "physical_name must look like logical__vN__model__dimension",
            details={"physical_name": physical_name},
        )
    logical, version, model, dimension = parts
    return IndexIdentity(
        logical_name=logical,
        schema_version=int(version[1:]),
        embedding_model_id=model,
        dimension=int(dimension),
        distance=fallback.distance,
    )


def _ollama(container: Any) -> Any:
    from ..llm.providers.ollama import OllamaProvider

    for provider in container.providers:
        if isinstance(provider, OllamaProvider):
            return provider
    return None


def register_admin_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    require_admin: Callable,
    require_super_admin: Callable,
    audit_admin: Callable,
    Body: Any,
    Depends: Any,
    HTTPException: Any,
    JSONResponse: Any,
    Query: Any,
    Request: Any,
    idempotent: Any = (),
) -> None:
    from pydantic import BaseModel, Field

    class SourceCreateRequest(BaseModel):
        # The id becomes a path segment (PATCH/DELETE /admin/sources/{source_id}), so it may not
        # contain anything a URL path treats specially -- '/', '?', '#', '%' -- nor be '.' or
        # '..' (audit INT-030). Starting with a letter or digit rules out the last two.
        id: str = Field(
            min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
        )
        type: str = "web"
        uri: str = Field(min_length=8, max_length=2000)
        trust_tier: int = 2
        enabled: bool = True
        max_documents: int = Field(default=1000, ge=1, le=50_000)
        max_bytes_per_document: int = Field(default=5 * 1024 * 1024, ge=1024)
        schedule_cron: str = "0 2 * * *"
        # Provenance (WP3). All optional: without a licence the source is registered but inert.
        name: str = Field(default="", max_length=200)
        publisher: str = Field(default="", max_length=200)
        domains: list[str] = Field(default_factory=list, max_length=20)
        language: str | None = Field(default=None, max_length=16)
        license_spdx: str | None = Field(default=None, max_length=64)
        attribution: str = Field(default="", max_length=500)

    class SourceProvenanceRequest(BaseModel):
        name: str | None = Field(default=None, max_length=200)
        publisher: str | None = Field(default=None, max_length=200)
        domains: list[str] | None = Field(default=None, max_length=20)
        language: str | None = Field(default=None, max_length=16)
        license_spdx: str | None = Field(default=None, max_length=64)
        attribution: str | None = Field(default=None, max_length=500)
        # True marks the source restricted (never ingested, whatever licence it states); False
        # lifts that and falls back to what the stated licence earns. Left out, a restricted
        # source stays restricted even when its licence text is corrected (D3).
        restricted: bool | None = None

    class SourceTakedownRequest(BaseModel):
        reason: str = Field(min_length=3, max_length=500)

    class RefreshTriggerRequest(BaseModel):
        # A dry run is the default: it fetches and gates every source and reports what would be
        # applied, and writes nothing. Changing the corpus has to be asked for.
        dry_run: bool = True

    class SourceEnableRequest(BaseModel):
        enabled: bool

    class IndexRollbackRequest(BaseModel):
        physical_name: str = Field(min_length=1, max_length=512)

    class ApprovalDecideRequest(BaseModel):
        approved: bool
        note: str = ""

    class ModelTransitionRequest(BaseModel):
        to: str
        approval_id: str | None = None
        canary_percent: int = Field(default=0, ge=0, le=100)
        force: bool = False

    class ModelRollbackRequest(BaseModel):
        to_version: str = Field(min_length=1, max_length=64)

    class LocalModelPullRequest(BaseModel):
        name: str = Field(min_length=1, max_length=200)

    class LearningApprovalRequest(BaseModel):
        base_model: str = ""

    class FineTuneJobRequest(BaseModel):
        approval_id: str = Field(min_length=1, max_length=128)
        base_model: str = ""
        run_inline: bool = True

    class ModelImportRequest(BaseModel):
        model_id: str = Field(default="aura-ft", min_length=1, max_length=128)
        version: str = Field(min_length=1, max_length=64)
        artefact_uri: str = Field(min_length=1, max_length=512)
        approval_id: str = Field(min_length=1, max_length=128)
        dataset_digest: str = Field(min_length=1, max_length=128)
        provider_kind: str = "ollama"
        base_model: str = ""
        notes: str = ""

    class PromotionApprovalRequest(BaseModel):
        model_id: str = Field(min_length=1, max_length=128)
        version: str = Field(min_length=1, max_length=64)
        summary: str = ""

    @router.get("/admin/audit")
    async def list_audit(
        caller=Depends(principal),
        actor: str | None = Query(default=None),
        action: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        require_admin(caller)
        events = await container.audit.query(actor=actor, action=action, limit=limit)
        return JSONResponse(content={"events": [event.as_dict() for event in events]})

    @router.get("/admin/sources")
    async def list_sources(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        registry = container.sources
        listing = getattr(registry, "list_all", None)
        sources = await listing() if listing else await registry.list_enabled()
        allowed = container.settings.knowledge.allowed_licenses
        return JSONResponse(content={"sources": [_source_payload(s, allowed) for s in sources]})

    @router.post("/admin/sources", dependencies=list(idempotent))
    async def create_source(
        request: Request, payload: SourceCreateRequest, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        source_type = SourceType(payload.type)
        owner_id = None
        if source_type is SourceType.CONNECTOR:
            # AP10: a connector's documents are its project's knowledge, and untrusted text. The
            # owner is never caller-chosen; the trust tier is capped rather than silently lowered.
            twins = getattr(container, "twins", None)
            if twins is None:
                raise HTTPException(status_code=503, detail="Track AP is not enabled on this install")
            if payload.trust_tier > int(TrustTier.COMMUNITY):
                raise ValidationError("connector content is untrusted: trust_tier must be 0 or 1",
                                      details={"trust_tier": payload.trust_tier})
            from ..adaptation.knowledge import project_owner

            owner_id = project_owner(twins.project_id)
        allowed = container.settings.knowledge.allowed_licenses
        # A connector's documents belong to its project, so the global licence policy does not
        # apply to it; for any other source a stated licence is checked before anything is stored.
        license_status = (
            LicenseStatus.UNKNOWN if owner_id is not None
            else _checked_licence(payload.license_spdx, allowed)
        )
        source = KnowledgeSource(
            id=payload.id,
            type=source_type,
            uri=payload.uri,
            enabled=payload.enabled,
            trust_tier=TrustTier(payload.trust_tier),
            owner_id=owner_id,
            max_documents=payload.max_documents,
            max_bytes_per_document=payload.max_bytes_per_document,
            schedule_cron=payload.schedule_cron,
            name=payload.name.strip(),
            publisher=payload.publisher.strip(),
            domains=_clean_domains(payload.domains),
            language=(payload.language or "").strip() or None,
            license_spdx=(payload.license_spdx or "").strip() or None,
            license_status=license_status,
            attribution=payload.attribution.strip(),
        )
        await container.sources.register(source)
        await audit_admin(
            request, caller, "admin.source.register", source.id, "ok",
            detail={"license_spdx": source.license_spdx, "license_status": str(source.license_status)},
        )
        return JSONResponse(content={"source": _source_payload(source, allowed)})

    @router.put("/admin/sources/{source_id}/provenance")
    async def update_source_provenance(
        request: Request,
        source_id: str,
        payload: SourceProvenanceRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        """Add or correct a source's provenance and licence (WP3, D-001).

        Naming a licence on the allow-list approves it; one outside the list is refused and
        nothing changes. Super Admin only: stating the licence attests the terms were read.
        """
        require_super_admin(caller)
        existing = await container.sources.get(source_id)
        if existing is None:
            raise NotFoundError(f"unknown source {source_id!r}")
        allowed = container.settings.knowledge.allowed_licenses
        license_status = None
        if payload.restricted is not None and existing.owner_id is not None:
            raise ValidationError("restricted applies to global sources only")
        if existing.owner_id is None:
            if payload.restricted is True:
                license_status = LicenseStatus.RESTRICTED
            elif payload.license_spdx is not None:
                checked = _checked_licence(payload.license_spdx, allowed)
                # A takedown is not undone by editing the licence text: lifting it is explicit.
                if existing.license_status is LicenseStatus.RESTRICTED and payload.restricted is not False:
                    license_status = None
                else:
                    license_status = checked
            elif payload.restricted is False:
                license_status = _checked_licence(existing.license_spdx, allowed)
        await container.sources.update_provenance(
            source_id,
            name=payload.name.strip() if payload.name is not None else None,
            publisher=payload.publisher.strip() if payload.publisher is not None else None,
            domains=_clean_domains(payload.domains) if payload.domains is not None else None,
            language=payload.language.strip() if payload.language is not None else None,
            license_spdx=payload.license_spdx.strip() if payload.license_spdx is not None else None,
            license_status=license_status,
            attribution=payload.attribution.strip() if payload.attribution is not None else None,
        )
        await audit_admin(
            request, caller, "admin.source.provenance", source_id, "ok",
            detail={"license_spdx": payload.license_spdx, "restricted": payload.restricted},
        )
        updated = await container.sources.get(source_id)
        return JSONResponse(content={"source": _source_payload(updated, allowed)})

    @router.patch("/admin/sources/{source_id}")
    async def enable_source(
        request: Request,
        source_id: str,
        payload: SourceEnableRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        require_admin(caller)
        await container.sources.set_enabled(source_id, payload.enabled)
        await audit_admin(
            request, caller, "admin.source.enable", source_id, "ok",
            detail={"enabled": payload.enabled},
        )
        source = await container.sources.get(source_id)
        allowed = container.settings.knowledge.allowed_licenses
        return JSONResponse(
            content={"source": _source_payload(source, allowed) if source else {"id": source_id}}
        )

    async def _retire(source: KnowledgeSource):
        """Retire a source's documents, then drop what the corpus's caches kept of them."""
        report = await retire_source_documents(
            container.knowledge, container.documents, source.id, owner_id=source.owner_id
        )
        if report.retired and container.cag is not None:
            await container.cag.invalidate(source.owner_id)
        return report

    @router.get("/admin/sources/{source_id}/documents")
    async def source_documents(source_id: str, caller=Depends(principal)) -> JSONResponse:
        """How many live documents a source has contributed: what the delete dialog asks about."""
        require_admin(caller)
        source = await container.sources.get(source_id)
        if source is None:
            raise NotFoundError(f"unknown source {source_id!r}")
        documents = await documents_of_source(
            container.documents, source.id, owner_id=source.owner_id
        )
        return JSONResponse(content={
            "source_id": source.id,
            "count": len(documents),
            "documents": [
                {"id": d.id.value, "title": d.title, "version": d.version} for d in documents[:20]
            ],
        })

    @router.delete("/admin/sources/{source_id}")
    async def delete_source(
        request: Request,
        source_id: str,
        documents: Literal["retire", "keep"] | None = Query(default=None),
        caller=Depends(principal),
    ) -> JSONResponse:
        """Remove a source; for a global one, say what happens to its documents (D3).

        A global source that has live documents is not deleted until the caller chooses:
        ``documents=retire`` removes them from the corpus first, ``documents=keep`` leaves them
        live and deletes only the registry row. Without a choice the answer is 409 with the count,
        so nothing is decided silently in either direction.
        """
        require_super_admin(caller)
        source = await container.sources.get(source_id)
        retirement = None
        if source is not None and source.owner_id is None:
            live = await documents_of_source(container.documents, source.id, owner_id=None)
            if live and documents is None:
                raise ConflictError(
                    "this source has live documents; choose documents=retire or documents=keep",
                    details={"source_id": source_id, "live_documents": len(live),
                             "choices": ["retire", "keep"]},
                )
            if live and documents == "retire":
                retirement = await _retire(source)
                if not retirement.complete:
                    # The row stays so the delete can be run again and finish the rest.
                    await audit_admin(
                        request, caller, "admin.source.delete", source_id, "partial",
                        detail=retirement.as_dict(),
                    )
                    raise KnowledgeError(
                        "some documents could not be retired; the source was not deleted",
                        details=retirement.as_dict(),
                    )
        removed = await container.sources.remove(source_id)
        await audit_admin(
            request, caller, "admin.source.delete", source_id, "ok" if removed else "missing",
            detail={"documents": documents, **(retirement.as_dict() if retirement else {})},
        )
        content: dict[str, Any] = {"deleted": bool(removed)}
        if retirement is not None:
            content["retirement"] = retirement.as_dict()
        return JSONResponse(content=content)

    @router.post("/admin/sources/{source_id}/takedown", dependencies=list(idempotent))
    async def takedown_source(
        request: Request,
        source_id: str,
        payload: SourceTakedownRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        """Withdraw a global source: stop fetching it and remove everything it contributed (D3).

        The source stays registered, marked ``restricted``, so the decision and its reason remain
        visible and a later licence edit cannot quietly bring it back. Order matters: the source
        is made unfetchable first, so a refresh running at the same moment cannot re-ingest what
        is being removed. Safe to run again after a partial result.
        """
        require_super_admin(caller)
        source = await container.sources.get(source_id)
        if source is None:
            raise NotFoundError(f"unknown source {source_id!r}")
        if source.owner_id is not None:
            raise ValidationError("takedown applies to global sources only")
        await container.sources.update_provenance(source_id, license_status=LicenseStatus.RESTRICTED)
        await container.sources.set_enabled(source_id, False)
        retirement = await _retire(source)
        await audit_admin(
            request, caller, "admin.source.takedown", source_id,
            "ok" if retirement.complete else "partial",
            detail={"reason": payload.reason, **retirement.as_dict()},
        )
        updated = await container.sources.get(source_id)
        allowed = container.settings.knowledge.allowed_licenses
        return JSONResponse(content={
            "source": _source_payload(updated, allowed) if updated else {"id": source_id},
            "retirement": retirement.as_dict(),
        })

    @router.post("/admin/knowledge/documents/{document_id}/cag-pin")
    async def admin_pin_cag(
        request: Request, document_id: str, caller=Depends(principal)
    ) -> JSONResponse:
        """Pin a global INTERNAL/VERIFIED document into every user's CAG handbook."""
        require_admin(caller)
        if container.cag is None:
            raise HTTPException(status_code=503, detail="CAG is disabled")
        from ..domain.identifiers import DocumentId

        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=None, pinned=True
        )
        await audit_admin(
            request, caller, "admin.cag.pin", document_id, "ok",
            detail={"version": document.version},
        )
        return JSONResponse(
            content={
                "document_id": document.id.value,
                "version": document.version,
                "cag_pinned": document.cag_pinned,
            }
        )

    @router.delete("/admin/knowledge/documents/{document_id}/cag-pin")
    async def admin_unpin_cag(
        request: Request, document_id: str, caller=Depends(principal)
    ) -> JSONResponse:
        require_admin(caller)
        if container.cag is None:
            raise HTTPException(status_code=503, detail="CAG is disabled")
        from ..domain.identifiers import DocumentId

        document = await container.cag.set_pin(
            DocumentId(document_id), owner_id=None, pinned=False
        )
        await audit_admin(request, caller, "admin.cag.unpin", document_id, "ok")
        return JSONResponse(
            content={
                "document_id": document.id.value,
                "version": document.version,
                "cag_pinned": document.cag_pinned,
            }
        )

    @router.get("/admin/indexes")
    async def list_indexes(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        active = await container.index_manager.active_identity()
        names_fn = getattr(container.vectors, "index_names", None)
        names = list(names_fn()) if names_fn else []
        if active and active.physical_name not in names:
            names.append(active.physical_name)
        return JSONResponse(
            content={
                "active": active.physical_name if active else None,
                "indexes": [
                    {"physical_name": name, "active": active is not None and name == active.physical_name}
                    for name in names
                ],
            }
        )

    @router.post("/admin/index/rollback")
    async def rollback_index(
        request: Request, payload: IndexRollbackRequest, caller=Depends(principal)
    ) -> JSONResponse:
        require_admin(caller)
        current = container.index_manager.identity_for_current_embedder()
        target = _parse_index_name(payload.physical_name, current)
        restored = await container.index_manager.rollback(target)
        await audit_admin(request, caller, "admin.index.rollback", restored.physical_name, "ok")
        return JSONResponse(content={"index": restored.physical_name, "rolled_back": True})

    refresh_runs = RefreshRunTracker(container.cache_backend)
    #: Strong references to in-flight refresh tasks: the event loop keeps only a weak one, so a
    #: task nobody holds can be collected before it finishes.
    background_refreshes: set[asyncio.Task] = set()

    async def _execute_refresh(run) -> None:
        try:
            report = await container.refresh_pipeline.run(dry_run=run.dry_run)
        except Exception as exc:
            logger.warning("triggered knowledge refresh failed", extra={"error": type(exc).__name__})
            await refresh_runs.finish(run, error=type(exc).__name__)
            return
        if not run.dry_run and container.cag is not None:
            # A real run can supersede a pinned global document; drop the prefix built from it.
            try:
                await container.cag.invalidate(None)
            except Exception:
                logger.warning("CAG invalidation after a refresh failed")
        await refresh_runs.finish(run, report=report.as_dict())

    @router.post("/admin/refresh", status_code=202)
    async def trigger_refresh(
        request: Request, payload: RefreshTriggerRequest, caller=Depends(principal)
    ) -> JSONResponse:
        """Start the knowledge refresh now, in the background, and return at once.

        The same pipeline the nightly job runs (ADR-008): fetch each enabled source, gate it,
        apply it, and rebuild the index only when it is stale. Needed on a deployment with no
        Celery worker, where nothing else would ever run it. A dry run (the default) needs Admin
        and writes nothing; a real run changes the live corpus and needs Super Admin. Only one
        run exists at a time: a second request while one is running is a 409 that names it.
        Poll ``GET /admin/refresh/latest`` for the outcome.
        """
        if payload.dry_run:
            require_admin(caller)
        else:
            require_super_admin(caller)
        actor = caller.email or caller.user_id.value
        run = await refresh_runs.start(dry_run=payload.dry_run, triggered_by=actor)
        if run is None:
            current = await refresh_runs.latest()
            raise ConflictError(
                "a knowledge refresh is already running",
                details={"run": current.as_dict() if current else None},
            )
        await audit_admin(
            request, caller, "admin.refresh.trigger", "knowledge", "started",
            detail={"run_id": run.run_id, "dry_run": run.dry_run},
        )
        task = asyncio.create_task(_execute_refresh(run))
        background_refreshes.add(task)
        task.add_done_callback(background_refreshes.discard)
        return JSONResponse(status_code=202, content={"run": run.as_dict()})

    @router.get("/admin/refresh/latest")
    async def latest_refresh(caller=Depends(principal)) -> JSONResponse:
        """The latest triggered run (shared across workers) and its report.

        ``report`` is that run's report when there is one, else this worker's last scheduled one,
        so the response keeps the shape it had before runs could be triggered.
        """
        require_admin(caller)
        run = await refresh_runs.latest()
        local = getattr(container.refresh_pipeline, "last_report", None)
        report = run.report if run is not None and run.report is not None else (
            local.as_dict() if local else None
        )
        return JSONResponse(content={
            "report": report,
            "run": run.as_dict() if run is not None else None,
        })

    @router.get("/admin/scorecard")
    async def scorecard(
        caller=Depends(principal), hours: int = Query(default=24, ge=1, le=24 * 30),
    ) -> JSONResponse:
        """Phase G1: the roadmap's own scorecard, live -- real turns from
        `container.telemetry_sink` over the trailing window, plus whatever eval suites have a
        recorded baseline (`evaluation/suites/*_baseline.json`, `RegressionGate.save_baseline`'s
        own format). Retrieval quality numbers are only as fresh as the last time someone ran
        that suite and saved its baseline; this endpoint reads what is on disk, it does not
        re-run the suite itself -- that stays a deliberate, explicit CI/operator action.
        """
        require_admin(caller)
        import json
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        turns = await container.telemetry_sink.recent_summary(since=since)

        suites_dir = Path(__file__).resolve().parents[2] / "evaluation" / "suites"
        retrieval: dict[str, Any] = {}
        if suites_dir.is_dir():
            for baseline_path in sorted(suites_dir.glob("*_baseline.json")):
                try:
                    payload = json.loads(baseline_path.read_text())
                except (OSError, ValueError):
                    continue
                suite = payload.get("suite", baseline_path.stem)
                retrieval[suite] = payload.get("metrics", {})

        return JSONResponse(
            content={
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window_hours": hours,
                "turns": turns,
                "retrieval": retrieval,
            }
        )

    @router.get("/admin/model-monitor")
    async def model_monitor_report(caller=Depends(principal)) -> JSONResponse:
        """The last production-inference monitoring pass (ADR-052): per-model metrics against their pinned
        baselines, and which ones drifted. Empty until the job (``AURA_MODEL_MONITOR_ENABLED``) has run."""
        require_admin(caller)
        from ..learning.model_monitor import last_report

        report = await last_report(container.cache_backend)
        return JSONResponse(content={
            "enabled": container.settings.learning.model_monitor_enabled, "report": report})

    @router.post("/admin/model-monitor/rebaseline")
    async def model_monitor_rebaseline(
        caller=Depends(principal), model: str | None = Query(default=None, max_length=80),
    ) -> JSONResponse:
        """Pin each monitored series' latest observation as its new baseline -- the deliberate step after an
        intended model change. Audited."""
        require_admin(caller)
        from ..learning.model_monitor import rebaseline
        from ..ports.runtime import AuditEvent

        result = await rebaseline(container.cache_backend, model=model)
        try:
            await container.audit.record(AuditEvent(
                action="model_monitor.rebaseline", actor=caller.email or caller.user_id.value,
                actor_role=caller.role, resource=f"model:{model or '*'}", outcome="ok",
                detail={"series": len(result["rebaselined"])}))
        except Exception:
            logger.warning("model monitor rebaseline audit write failed", exc_info=False)
        return JSONResponse(content=result)

    @router.get("/admin/approvals")
    async def list_approvals(
        caller=Depends(principal),
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None),
    ) -> JSONResponse:
        require_admin(caller)
        from ..learning.approval import ApprovalKind, ApprovalStatus

        status_enum = ApprovalStatus(status) if status else None
        kind_enum = ApprovalKind(kind) if kind else None
        rows = await container.approvals._store.list(status=status_enum, kind=kind_enum)
        return JSONResponse(content={"approvals": [row.as_dict() for row in rows]})

    @router.post("/admin/approvals/{approval_id}/decide", dependencies=list(idempotent))
    async def decide_approval(
        request: Request,
        approval_id: str,
        payload: ApprovalDecideRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        require_admin(caller)
        # Track AP's gates are a Super Admin's decision by design (ADR-021 section 5, ADR-024
        # section 3; ``learning/approval.py``). Activate and deploy already require SuperAdmin;
        # without this an Admin could still be the person who approved or rejected them (audit
        # INT-006). Rejecting is a decision too, so both directions are refused.
        existing = await container.approvals.get(approval_id)
        if existing is not None and existing.kind in _SUPER_ADMIN_APPROVAL_KINDS:
            require_super_admin(caller)
        decided = await container.approvals.decide(
            approval_id,
            approved=payload.approved,
            by=caller.email or caller.user_id.value,
            note=payload.note,
        )
        await audit_admin(
            request, caller, "admin.approval.decide", approval_id,
            "approved" if payload.approved else "rejected",
        )
        return JSONResponse(content={"approval": decided.as_dict()})

    @router.get("/admin/models")
    async def list_models(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        versions = await container.models.list_versions()
        return JSONResponse(content={"models": [version.as_dict() for version in versions]})

    @router.post("/admin/models/{model_id}/transition")
    async def transition_model(
        request: Request,
        model_id: str,
        payload: ModelTransitionRequest,
        caller=Depends(principal),
        version: str = Query(min_length=1),
    ) -> JSONResponse:
        require_admin(caller)
        promoted = await container.models.transition(
            model_id,
            version,
            to=ModelStage(payload.to),
            approval_id=payload.approval_id,
            canary_percent=payload.canary_percent,
            actor=caller.email or caller.user_id.value,
            force=payload.force,
        )
        await audit_admin(request, caller, "admin.model.transition", promoted.key, "ok")
        return JSONResponse(content={"model": promoted.as_dict()})

    @router.post("/admin/models/{model_id}/rollback")
    async def rollback_model(
        request: Request,
        model_id: str,
        payload: ModelRollbackRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        require_admin(caller)
        restored = await container.models.rollback(
            model_id,
            to_version=payload.to_version,
            actor=caller.email or caller.user_id.value,
        )
        await audit_admin(request, caller, "admin.model.rollback", restored.key, "ok")
        return JSONResponse(content={"model": restored.as_dict()})

    @router.get("/admin/learning/recommendation")
    async def learning_recommendation(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        recommendation, stored = await container.finetune.recommend(persist=True)
        payload = recommendation.as_dict()
        if stored is not None:
            payload["dataset"] = stored.as_dict()
        return JSONResponse(content={"recommendation": payload})

    @router.post("/admin/learning/request-approval", dependencies=list(idempotent))
    async def learning_request_approval(
        request: Request,
        payload: LearningApprovalRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        require_admin(caller)
        approval, recommendation = await container.finetune.request_approval(
            base_model=payload.base_model or None,
        )
        await audit_admin(
            request, caller, "admin.learning.request_approval", approval.id, "ok",
            detail={"digest": approval.artefact_digest},
        )
        return JSONResponse(
            content={
                "approval": approval.as_dict(),
                "recommendation": recommendation.as_dict(),
            }
        )

    @router.get("/admin/learning/datasets/{digest}")
    async def learning_export_dataset(
        digest: str,
        caller=Depends(principal),
        format: str = Query(default="chatml"),
    ) -> JSONResponse:
        require_admin(caller)
        text = await container.datasets.export(digest, fmt=format)
        return JSONResponse(
            content={"digest": digest, "format": format, "jsonl": text}
        )

    @router.post("/admin/learning/jobs", dependencies=list(idempotent))
    async def launch_finetune_job(
        request: Request,
        payload: FineTuneJobRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        require_super_admin(caller)
        job = await container.finetune_jobs.enqueue(
            approval_id=payload.approval_id,
            base_model=payload.base_model,
        )
        await audit_admin(
            request, caller, "admin.learning.job_enqueue", job.id, "ok",
            detail={"approval": payload.approval_id, "digest": job.dataset_digest},
        )
        if payload.run_inline:
            # Runs on the gateway's own trainer, which is always `scripted` in both compose
            # files: the job ends NOT_TRAINED with the reason in `error` and registers no model.
            # Real LoRA needs run_inline=false: the job stays QUEUED until worker-training
            # (compose profile `training`, the `training` Celery queue) runs
            # tasks.finetune_job_task / drain_finetune_queue_task against it.
            job = await container.finetune_jobs.run(job.id)
        else:
            logger.info(
                "fine-tune job queued for the training worker",
                extra={"job": job.id},
            )
        return JSONResponse(content={"job": job.as_dict()})

    @router.get("/admin/learning/jobs")
    async def list_finetune_jobs(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        rows = await container.finetune_jobs.list()
        return JSONResponse(content={"jobs": [j.as_dict() for j in rows]})

    @router.get("/admin/learning/jobs/{job_id}")
    async def get_finetune_job(job_id: str, caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        job = await container.finetune_jobs.get(job_id)
        return JSONResponse(content={"job": job.as_dict()})

    @router.post("/admin/models/import", dependencies=list(idempotent))
    async def import_model_artefact(
        request: Request,
        payload: ModelImportRequest,
        caller=Depends(principal),
    ) -> JSONResponse:
        """Register a locally available Ollama/GGUF tag as CANDIDATE without training.

        Escape hatch for CPU VPS: train on a GPU box, import the tag here, then
        promote through the same approval gates.
        """
        require_super_admin(caller)
        await container.approvals.require(
            payload.approval_id, expected_digest=payload.dataset_digest
        )
        from ..mlops.model_registry import ModelStage, ModelVersion

        version = ModelVersion(
            model_id=payload.model_id,
            version=payload.version,
            provider_kind=payload.provider_kind,
            base_model=payload.base_model,
            stage=ModelStage.CANDIDATE,
            artefact_uri=payload.artefact_uri,
            dataset_digest=payload.dataset_digest,
            approval_id=payload.approval_id,
            notes=payload.notes or "imported artefact",
        )
        registered = await container.models.register(version)
        await audit_admin(
            request, caller, "admin.model.import", registered.key, "ok",
            detail={"artefact": payload.artefact_uri, "digest": payload.dataset_digest},
        )
        return JSONResponse(content={"model": registered.as_dict()})

    @router.post("/admin/models/{model_id}/request-promotion", dependencies=list(idempotent))
    async def request_model_promotion(
        request: Request,
        model_id: str,
        payload: PromotionApprovalRequest,
        caller=Depends(principal),
        version: str = Query(min_length=1),
    ) -> JSONResponse:
        """Create a MODEL_PROMOTION approval bound to model_id@version."""
        require_admin(caller)
        from ..learning.approval import ApprovalKind

        current = await container.models.get(model_id, version)
        summary = payload.summary or f"Promote {current.key} to production"
        approval = await container.approvals.request(
            kind=ApprovalKind.MODEL_PROMOTION,
            summary=summary,
            artefact_digest=current.key,
            payload={
                "model_id": model_id,
                "version": version,
                "artefact_uri": current.artefact_uri,
                "dataset_digest": current.dataset_digest,
                "metrics": dict(current.metrics),
            },
            requested_by=caller.email or caller.user_id.value,
        )
        await audit_admin(
            request, caller, "admin.model.request_promotion", current.key, "ok",
            detail={"approval": approval.id},
        )
        return JSONResponse(content={"approval": approval.as_dict(), "model": current.as_dict()})

    @router.get("/admin/local-models")
    async def list_local_models(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        provider = _ollama(container)
        if provider is None:
            return JSONResponse(content={"models": [], "available": False})
        models = await provider.list_models()
        return JSONResponse(
            content={
                "available": True,
                "models": [
                    {
                        "name": m.name,
                        "size_bytes": m.size_bytes,
                        "parameter_size": m.parameter_size,
                        "quantization": m.quantization,
                        "family": m.family,
                        "loaded": m.loaded,
                    }
                    for m in models
                ],
            }
        )

    @router.post("/admin/local-models/pull")
    async def pull_local_model(
        request: Request, payload: LocalModelPullRequest, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        provider = _ollama(container)
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail=AuthorizationError("no local Ollama provider is configured").to_dict(),
            )
        last: dict[str, Any] = {}
        async for event in provider.pull_model(payload.name):
            last = dict(event)
        await audit_admin(request, caller, "admin.local_model.pull", payload.name, "ok")
        return JSONResponse(content={"pulled": payload.name, "progress": last})

    # ``:path`` so a namespaced Ollama tag (``library/llama3:8b``, ``user/model``) reaches the
    # handler: the gateway sees the path already percent-decoded, so ``%2F`` is a real '/'
    # by the time routing runs (audit INT-031).
    @router.delete("/admin/local-models/{name:path}")
    async def delete_local_model(
        request: Request, name: str, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        provider = _ollama(container)
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail=AuthorizationError("no local Ollama provider is configured").to_dict(),
            )
        deleted = await provider.delete_model(name)
        await audit_admin(request, caller, "admin.local_model.delete", name, "ok")
        return JSONResponse(content={"deleted": bool(deleted)})

