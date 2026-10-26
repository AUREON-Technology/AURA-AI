# Audit Manifest — `2026-10-26-full-699f27b`

## Baseline
| Field | Value |
|-------|-------|
| Repository | `anasibrahimgnedy-collab/AURA-AI` |
| Branch | `ccr-4760e151-1si7gz` (at the same commit as `main` merge #181) |
| Commit | `699f27bad214516d3f98f7371f5a1c5cf3550ee5` (2026-10-19) |
| Working tree | clean before and after (`git status --porcelain` empty except these audit files) |
| Stacks | Python 3 / FastAPI / SQLAlchemy / asyncpg / Celery; Flutter (Dart 3.12 pin); Next.js; pure Dart hostd; Docker Compose + nginx |
| Environment | headless cloud container, Python 3.13, Node 22; **no** backend deps, Flutter or Dart SDK installed |
| Invocation | `/system-audit objective="Find UI/UX, business logic, user flow, architecture, security, performance and reliability issues, and recommend the best upgrades with a prioritized implementation roadmap" mode=full` |
| Skill resources missing | `references/audit-checklist.md` and `references/report-template.md` were not supplied; `SKILL.md` rules were applied directly |

## Coverage matrix

Legend: ● inspected in depth · ◐ partial or spot-checked · ○ not inspected

| Area | UX | Flows | Security | Arch/Data | Perf/Rel/CI |
|------|----|-------|----------|-----------|-------------|
| `aura_core/api/v3.py`, `admin*.py`, `host_agent.py`, `voice_live.py` | ◐ | ● | ● | ◐ | ◐ |
| `aura_core/kernel`, `planning`, `execution`, `tools` | — | ● | ● | ◐ | ● |
| `aura_core/llm` (router, circuit, cache, sensitivity) | — | ◐ | ● | ● | ● |
| `aura_core/knowledge`, `rag`, `memory` | — | ● | ◐ | ● | ◐ |
| `aura_core/adapters` (postgres, qdrant, docker_sandbox) | — | ◐ | ◐ | ● | ◐ |
| `aura_core/observability`, `container.py` | — | ◐ | — | ● | ● |
| `aura_core/adaptation`, connectors (Track AP) | — | ○ | ◐ (SQL only) | ○ | ○ |
| `api/v2` (auth, sync, insights, github), `services/`, `infrastructure/` | — | ● | ● | ◐ | ◐ |
| legacy `tasks.py`, `celery_app.py` | — | ◐ | — | ● | ● |
| Alembic `versions/` | — | ◐ | — | ● | — |
| `aura_client/lib/features/*`, `core/*` | ● | ● | ◐ | ◐ (contracts) | ◐ |
| `aura_client/lib/desktop` | ◐ | — | — | — | — |
| `aura_admin/` | ◐ (auth/refresh) | ○ | ○ | ○ | ◐ (CI) |
| `aura_hostd/` | — | — | ● (policy, control server) | ● (protocol) | ● (reconnect) |
| `deploy/` compose, nginx, scripts | — | — | ● | ◐ | ● |
| `.github/workflows/` | — | — | ● | — | ● |
| `infra/` Terraform, `docs/archive/`, ML training | ○ | ○ | ○ | ○ | ○ |

## Commands run (all read-only)
| Command | Exit | Result |
|---------|------|--------|
| `git status -sb`, `git log --oneline -5`, `git log -1 --format='%H %cd'` | 0 | baseline above |
| `git grep -n GOCSPX` (output redacted) | 0 | no live secret; comments, docs and test regex only |
| `git ls-files \| grep -Ei '\.(exe\|log\|apk\|db\|sqlite)$'`; `du -h` on the APK | 0 | APK 72 MB, `aura_hostd/build/aura_hostd.log` tracked |
| `grep -hE '^(revision\|down_revision)' alembic/versions/*.py` | 0 | linear 0001→0013 |
| `python3 -B repro_breaker.py aura_backend` | 0 | breaker stuck `half_open`/`circuit_open` at +1h and +24h |
| `python3 -B repro_hostd.py aura_backend` | 0 | non-streaming run → `'' -1` |
| `python3 -B repro_lexical.py aura_backend` | 0 | deleted chunk returned on second engine |
| many `grep -n` / `sed -n` evidence reads by the main auditor and five lens agents | 0 | cited in findings |

## Commands NOT run, and why
| Check | Reason |
|-------|--------|
| `pytest`, `pytest tests/architecture`, legacy `unittest` | backend dependencies not installed; the skill forbids installing packages |
| `flutter analyze`, `flutter test`, `build_runner` | no Flutter SDK; installing one would alter setup for the audit |
| `dart analyze`, `dart test` (hostd) | no Dart SDK |
| `npm run lint`, `npm run test:e2e` (admin) | `node_modules` absent; installing is forbidden |
| `tests/load`, `tests/benchmarks`, testcontainers | need Docker or real services |
| Live VPS (`/opt/aura-stack` SHA, logs, secret paths), GitHub Actions run history, branch protection, Firebase console | outside read-only local scope; listed in the report as follow-up evidence |

## Files written by this audit (the only writes)
- `.claude/audits/2026-10-26-full-699f27b/audit-report.md`
- `.claude/audits/2026-10-26-full-699f27b/remediation-roadmap.md`
- `.claude/audits/2026-10-26-full-699f27b/audit-manifest.md`
- `.claude/audits/2026-10-26-full-699f27b/lens-evidence/lens-{ux,flows,security,arch,perf}.md`. These are raw lens reports. The consolidated report supersedes their severities where they differ; FL-01 in particular was downgraded, see F-13.
- `.claude/audits/2026-10-26-full-699f27b/lens-evidence/repro_{breaker,hostd,lexical}.py`. These are stdlib-only reproduction scripts; run them with `python3 -B <script> <path-to-aura_backend>`.

## Cross-lens reconciliation notes
- **FL-01 (Critical) vs UX-07 (Medium):** the same code path. The main auditor confirmed that the UUID guard at `api/v2/sync.py:167-171` rejects the row before the NOT NULL insert, so FL-01's "push 500 forever" does not occur today. The two are merged as F-13 (Medium), with an explicit coupling note.
- **Duplicates merged:**
  - FL-14 = SEC-02 → F-02.
  - FL-06 = AR-03 → F-14.
  - AR-05 = PR-06 → F-20.
  - FL-10 + UX-04 + UX-05 + SEC-07 + CB-04 → F-04.
  - FL-17 + SEC-05 + SEC-06 + AR-15 → F-09.
  - AR-01 + AR-07 + PR-05 → F-19.
- **Backlog line numbers corrected:**
  - CB-14 is now at `kernel.py:1401-1403` and `:642-644`.
  - The CB-13 hostd binary moved to `aura_hostd/aura_hostd`.
  - CB-20 counts are now 33 runtime tables, 16 without a migration.
