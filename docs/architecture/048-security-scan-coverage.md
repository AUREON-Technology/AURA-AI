# ADR-048: Security scan coverage — one credential catalogue, per-language rules, a local advisory copy

Status: Proposed (built, not proven against a live deployment). Extends ADR-036 and ADR-045.

## Problem

`scan_code_security` knew seven Python constructs and four secret shapes, kept a second secret list
apart from `SecretGuard`, had no vulnerability data (it makes no network call), and no external
scanner (the gateway does not run subprocesses). "No findings" therefore proved little.

## Decisions

1. **One credential catalogue** — `aura_core/domain/secret_catalog.py` (stdlib only), read by both
   `SecretGuard` and the scanner: ~50 vendor formats, PEM blocks through their footer, JWTs, URL
   credentials, name-based assignments (quoted and `.env`/YAML-style) with placeholder filtering, and an
   entropy rule for soft names. Findings carry a value-free `fingerprint`; a `pragma: allowlist secret`
   / `gitleaks:allow` / `aura:allow-secret` comment or a baseline file accepts reviewed ones. Snippets
   mask every credential-named value, header and flag; `SEC-SH-006` withholds its line.
2. **Rule packs** — `aura_core/tools/open/security_rules.py`: Python (+12), JavaScript/TypeScript, Go,
   Java/Kotlin, shell, Dockerfile, Kubernetes/Compose/GitHub-Actions YAML, Terraform (77 rules). Line
   rules plus a few file rules (no `USER`, floating tags, `cap_add`, workflow script injection). It
   finds constructs, not data flow, and says so in its result.
3. **A local advisory copy** — `aura_osv_advisories` / `aura_osv_sync` (migration 0031), written by a
   scheduled job (`learning/osv_sync.py`, Celery `tasks.osv_sync_task`) and read by the scan. The job is
   **off by default** (`AURA_OSV_SYNC_ENABLED`), may contact **one host** (`storage.googleapis.com`, fixed
   in `domain/advisories.FEED_HOST`, https only, no redirects, size-capped), and is the only network
   call involved; the scan and the tools still make none. First run per ecosystem downloads the bulk
   archive, later runs fetch only what the feed lists as modified. `MAL-*` malicious-package reports are
   skipped unless `AURA_OSV_INCLUDE_MALWARE`. Every scan reports how old the copy is (`stale` past
   `AURA_OSV_MAX_AGE_DAYS`), whether an ecosystem was never downloaded (`checked: false`, packages
   *not* checked), and the last sync error.

## Consequences / limits

* A VPS worker is on the internal `data` network, which has no route out: enabling the job there needs
  an egress route to that one host (not added here: the compose parity tests treat network changes as
  security-relevant). Railway workers can reach it.
* Advisories published since the last sync are unknown until the next one.
* PyPI and npm only (what `workspace_deps` can match).
* Semgrep / Gitleaks / Bandit in the sandbox, and continuous monitoring, are separate steps.

## Evidence

Unit tests: `test_secret_catalog.py`, `test_security_rule_packs.py` (every rule has a firing case and
lookalikes; hostile 2,000-character lines are timed), `test_security_scan_baseline.py`,
`test_osv_advisories.py` (store contract on memory and real Postgres; feed and sync against a local
server serving the bucket's layout), `test_scan_against_local_advisories.py`. Real run on 2026-11-25:
bootstrap of the live PyPI feed into Postgres in 8 s (15,477 advisories stored, 12,070 `MAL-` skipped),
an unchanged re-run in 0.4 s, and a lockfile with `django==3.0.0` / `requests==2.19.0` matched against
real CVEs.
