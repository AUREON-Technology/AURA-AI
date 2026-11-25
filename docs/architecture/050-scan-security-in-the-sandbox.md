# ADR-050: `scan_security` — Semgrep, Gitleaks and Bandit in the sandbox

Status: Proposed (built; parsers verified on real tool output and the tool on real Bandit, Gitleaks and Semgrep
runs; the sandbox image build itself is not yet run). Extends ADR-048 (the in-gateway pattern scanner).

## Problem

`scan_code_security` is a pattern scanner inside the gateway. The gateway must not run subprocesses, so the
stronger scanners (Semgrep for data-flow-aware rules across languages, Gitleaks for secrets and git history,
Bandit for Python) were never available, and "no findings" proved little.

## Decision (`tools/devtools_security.py`, `tools/devtools.py`, `deploy/sandbox/Dockerfile`)

`scan_security` runs them through the same `SandboxRunner` as `run_command` / `lint_code` (the paired desktop or
the server sandbox), and returns structured findings with one status per scanner. Contract, as in `lint_code`:
`clean` only when every requested scanner ran fully and found nothing; missing, timed out, crashed, unreadable
output, or "could not analyse some files" (`incomplete`) is never clean.

* **Rules are the image's, not the repository's.** Semgrep uses the rule set the image bundles
  (`/opt/aura/semgrep-rules`, a checkout pinned at one commit), never `--config auto` (network, telemetry), with
  metrics and version check off, `.semgrepignore` files ignored (found by a real run: a workspace
  `.semgrepignore` containing `*` otherwise hid all code) and `.gitignore` not honoured.
* **No secret value is returned.** Gitleaks runs with `--redact` and only rule, place, fingerprint and a
  12-character commit prefix are read; Semgrep/Bandit snippets go through the shared credential catalogue.
* **Pinned and verified.** Semgrep 1.187.0 and Bandit 1.9.4 in their own virtualenv; Gitleaks 8.28.0 checked
  against its published sha256. About +300 MB.
* `workspace_local` like `lint_code`: read-only, confined to the workspace, no network.

## Limits

* On a paired desktop the scanners (and the bundled rules path) must exist there; otherwise the status is
  `not_installed` / `failed`, never clean.
* `--x-ignore-semgrepignore-files` is an *internal* Semgrep flag: pinned version only, and a real-run test
  (`test_a_rule_file_in_the_repository_is_not_used`) fails if an upgrade breaks it.
* Semgrep loads thousands of rules per run (about a minute or more on a small repository).

## Evidence

`tests/unit/test_scan_security.py` (parsers on `tests/fixtures/scanners/*`, real tool runs that skip rather than
fake when a scanner is absent, the empty-PATH not-installed case). Not yet run: the image build.
