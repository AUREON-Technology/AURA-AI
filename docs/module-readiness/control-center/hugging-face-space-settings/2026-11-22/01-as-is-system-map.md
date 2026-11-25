# 01 As-is system map

## Stack and layers

| Layer | Path | Stack | Verify with |
|---|---|---|---|
| Console UI | `aura_admin/app/(console)/settings/space/page.tsx` | Next.js (React), i18n en/ar | `npm run lint`, `npm run test:e2e` (7 GPU Space tests, mock gateway) |
| Gateway routes | `aura_backend/aura_core/api/admin.py` (`/admin/control/space/*`) | FastAPI | `pytest` |
| Engine | `aura_backend/aura_core/control/space.py` (`SpaceService`) | Python, engine layer, port-only | `pytest tests/unit/test_control_space.py` |
| Adapter | `aura_backend/aura_core/adapters/hf_space.py` (`HfSpaceHost`, `InMemorySpace`) | `huggingface_hub` 1.28, lazy import, worker thread | same |
| Deploy | `.github/workflows/inference-plane-deploy.yml`, `deploy/spaces/inference-vllm/` | GitHub Actions, `HfApi` | `tests/deployment/test_inference_contract.py` |
| Docs | `docs/deployment/inference-plane.md`, `docs/plans/console-control-center-plan.md`, ADR-030, ADR-037 | Markdown | n/a |

## The failing flow (Resume)

`page.tsx:295-299` (shown only when stage is PAUSED/STOPPED) -> typed-name confirm + step-up sign-in
-> `POST /admin/control/space/restart {confirm}` -> `SpaceService.restart` (`space.py:220`):
`_guard` (name match, write cap) -> `_not_busy` -> `HfSpaceHost.restart` -> `HfApi.restart_space`
on a worker thread -> any 4xx other than 401/403/404 raises `ValidationError("... refused the
request (HTTP N)", reason="hf_refused")` (`hf_space.py:83-86`) -> dialog shows that text.
The journal entry (`space.py:225`) is written **only after** the Hub call succeeds.

## Where the stored decisions disagree

| Source | Hardware | Sleep | Authority |
|---|---|---|---|
| `inference-plane.md:152` (§3) | `a10g-small` | sleeps when idle | owner, 2026-11-17 |
| `inference-plane.md:223` (§9) | `l4x1`, $0.80/h, about $584/month | never | owner, 2026-11-18 |
| `inference-plane-deploy.yml:149-162` | `l4x1` (or `vars.HF_SPACE_HARDWARE`) | `-1` | enforces §9 on each push |
| Live Hub record, 2026-11-22 | `a10g-small` requested, none running, `PAUSED` | `gcTimeout 1800` | observed |
| **D1, 2026-11-22** | **none** | n/a | **owner, this session** |

## Live gateway (public `/health/ready`, 2026-11-22)

`healthy: true`; `hosted1` healthy; `hfspace` unhealthy "HTTP 503" (active probe of a paused
Space); `claude1` healthy; schema `0032`; release `93635069680e`. Adjacent, out of scope:
`knowledge_index.up_to_date: false` (published `hash-embed-v1`, required `intfloat-multilingual-e5-small`).

## Evidence map

| Area | Inspected | Confidence |
|---|---|---|
| UI | `page.tsx` in full, `errors.ts:34-47`, `change-tray.tsx` | High |
| Engine / adapter | `space.py`, `hf_space.py` in full | High |
| Tests | `test_control_space.py:255-304`, e2e test titles | Medium (titles and one body read) |
| Routes | `api/admin.py` not read | Low |
| Deploy | workflow lines 12-27, 128-167 | Medium |
| Live Hub / gateway | two public GETs | High for what they show |
| Hub 402 body, token scope | not observable | None |
