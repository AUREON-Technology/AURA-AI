"""Operator HTTP surface for kernel services already on the container.

Kept out of ``v3.py`` so the gateway adapter does not become a monolith.
Every mutating handler writes an audit event. SuperAdmin-only routes are
marked with ``require_super_admin``.
"""

from typing import Any, Callable

from ..domain.errors import AuthorizationError, ValidationError
from ..domain.knowledge import IndexIdentity, KnowledgeSource, SourceType, TrustTier
from ..mlops.model_registry import ModelStage
from ..observability.logging import get_logger

logger = get_logger("api.v3.admin")


def _source_payload(source: KnowledgeSource) -> dict[str, Any]:
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
    }


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
        id: str = Field(min_length=1, max_length=128)
        type: str = "web"
        uri: str = Field(min_length=8, max_length=2000)
        trust_tier: int = 2
        enabled: bool = True
        max_documents: int = Field(default=1000, ge=1, le=50_000)
        max_bytes_per_document: int = Field(default=5 * 1024 * 1024, ge=1024)
        schedule_cron: str = "0 2 * * *"

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
        return JSONResponse(content={"sources": [_source_payload(s) for s in sources]})

    @router.post("/admin/sources", dependencies=list(idempotent))
    async def create_source(
        request: Request, payload: SourceCreateRequest, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        source = KnowledgeSource(
            id=payload.id,
            type=SourceType(payload.type),
            uri=payload.uri,
            enabled=payload.enabled,
            trust_tier=TrustTier(payload.trust_tier),
            max_documents=payload.max_documents,
            max_bytes_per_document=payload.max_bytes_per_document,
            schedule_cron=payload.schedule_cron,
        )
        await container.sources.register(source)
        await audit_admin(request, caller, "admin.source.register", source.id, "ok")
        return JSONResponse(content={"source": _source_payload(source)})

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
        return JSONResponse(content={"source": _source_payload(source) if source else {"id": source_id}})

    @router.delete("/admin/sources/{source_id}")
    async def delete_source(
        request: Request, source_id: str, caller=Depends(principal)
    ) -> JSONResponse:
        require_super_admin(caller)
        removed = await container.sources.remove(source_id)
        await audit_admin(request, caller, "admin.source.delete", source_id, "ok" if removed else "missing")
        return JSONResponse(content={"deleted": bool(removed)})

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

    @router.get("/admin/refresh/latest")
    async def latest_refresh(caller=Depends(principal)) -> JSONResponse:
        require_admin(caller)
        report = getattr(container.refresh_pipeline, "last_report", None)
        return JSONResponse(content={"report": report.as_dict() if report else None})

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
            # Gateway-inline for ScriptedTrainer / tests. Heavy LocalLoraTrainer
            # should set run_inline=false; the learning Celery worker runs
            # tasks.finetune_job_task against the queued job id (outside aura_core).
            job = await container.finetune_jobs.run(job.id)
        else:
            logger.info(
                "fine-tune job queued for learning worker",
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

    @router.delete("/admin/local-models/{name}")
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

