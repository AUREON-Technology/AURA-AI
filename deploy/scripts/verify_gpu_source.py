"""SaaS P1-G live checks: the always-on GPU source and the readiness floor it unblocks.

Collects the evidence for these items in spec §6.1 (`docs/superpowers/specs/2026-11-16-saas-phase1-design.md`):

* **#9 the sensitive-capable GPU source is live and healthy:** checks 1, 2, 3 and 6;
* **#3 `/metrics` and `/docs` are not public:** check 4;
* **#2 no training-permitted model serves customers:** check 5;
* **#6 capacity at the launch target:** check 7, 10 concurrent turns by default
  (owner decision 2026-11-18).

Run it from anywhere that reaches the gateway, the Space and huggingface.co. It needs only the
standard library:

    AURA_VERIFY_GATEWAY=https://api.aureon-ai.tech AURA_VERIFY_TOKEN=<operator or Pro user token> \\
    AURA_VERIFY_ADMIN_TOKEN=<Admin token> \\
    AURA_VERIFY_SPACE_URL=https://<owner>-<space>.hf.space AURA_VERIFY_SPACE_KEY=<VLLM_API_KEY> \\
    AURA_VERIFY_HF_TOKEN=<HF read token> AURA_VERIFY_SPACE_ID=<owner>/<space> \\
        python deploy/scripts/verify_gpu_source.py

It writes `docs/reports/readiness-gpu-<UTC>.txt`; commit that file. Exit code = failed checks
(at most 125). A check whose credential was not given is SKIP. SKIP is not a pass: the report
then says NOT PROVEN.

It spends little. It ingests one small document and deletes it again (a failed delete fails check 6),
sends one document turn,
and sends AURA_VERIFY_CONCURRENCY short turns. It never prints a token or the Space key.
"""

from __future__ import annotations

import json
import math
import os
import random
import ssl
import statistics
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPORT_DIR_USED = ""


def percentile_95(values: list[float]) -> float:
    """Nearest rank: the smallest value at or above 95 % of the sample."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
_TIMEOUT_S = 180.0


def _request(method: str, url: str, *, token: str = "", body: dict | None = None,
             timeout: float = _TIMEOUT_S) -> tuple[int, object, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    started = time.perf_counter()
    context = ssl.create_default_context() if url.startswith("https:") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    took = time.perf_counter() - started
    try:
        return status, json.loads(raw or b"null"), took
    except ValueError:
        return status, raw.decode("utf-8", "replace")[:300], took


class _Skipped(Exception):
    """A check that cannot run with what was given. Reported as SKIP, never as a pass."""


class Verifier:
    def __init__(self, env: dict[str, str]) -> None:
        self.gateway = env.get("AURA_VERIFY_GATEWAY", "").rstrip("/")
        self.token = env.get("AURA_VERIFY_TOKEN", "")
        self.tokens = [self.token, *[t for t in env.get("AURA_VERIFY_EXTRA_TOKENS", "").split(",") if t]]
        self.admin = env.get("AURA_VERIFY_ADMIN_TOKEN", "")
        self.space = env.get("AURA_VERIFY_SPACE_URL", "").rstrip("/")
        self.space_key = env.get("AURA_VERIFY_SPACE_KEY", "")
        self.hf_token = env.get("AURA_VERIFY_HF_TOKEN", "")
        self.space_id = env.get("AURA_VERIFY_SPACE_ID", "")
        self.hf_api = env.get("AURA_VERIFY_HF_API", "https://huggingface.co").rstrip("/")
        self.hardware = env.get("AURA_VERIFY_EXPECT_HARDWARE", "l4x1")
        self.concurrency = int(env.get("AURA_VERIFY_CONCURRENCY", "10"))
        self.p95_limit = float(env.get("AURA_VERIFY_P95_S", "60"))
        self.provider = env.get("AURA_VERIFY_EXPECT_PROVIDER", "hfspace")
        self.lines: list[str] = []
        self.results: list[str] = []
        self._secrets = [s for s in (self.token, self.admin, self.space_key, self.hf_token, *self.tokens) if s]

    # -- reporting ------------------------------------------------------------------------

    def _out(self, line: str) -> None:
        for secret in self._secrets:
            line = line.replace(secret, "<redacted>")
        print(line)
        self.lines.append(line)

    def run_check(self, number: int, name: str, fn) -> None:
        try:
            detail = fn() or ""
            status = "PASS"
        except _Skipped as exc:
            status, detail = "SKIP", str(exc)
        except AssertionError as exc:
            status, detail = "FAIL", str(exc)
        except Exception as exc:  # a crash is a failure, with its cause
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"
        self.results.append(status)
        self._out(f"{status:<6} {number}. {name}")
        for line in str(detail).splitlines():
            self._out(f"         {line}")

    # -- the checks -------------------------------------------------------------------------

    def c1_space_authenticated(self) -> str:
        if not (self.space and self.space_key):
            raise _Skipped("AURA_VERIFY_SPACE_URL / AURA_VERIFY_SPACE_KEY not given")
        status, _, _ = _request("GET", f"{self.space}/v1/models")
        assert status in (401, 403), f"the Space answered {status} without a key: it is open to anyone"
        status, body, took = _request("GET", f"{self.space}/v1/models", token=self.space_key)
        assert status == 200, f"with the key: HTTP {status} {body}"
        models = [m.get("id") for m in (body or {}).get("data", [])] if isinstance(body, dict) else []
        assert models, f"the Space lists no model: {body}"
        return f"unauthenticated refused; serves {models} ({took:.2f}s)"

    def c2_always_on_gpu(self) -> str:
        if not (self.hf_token and self.space_id):
            raise _Skipped("AURA_VERIFY_HF_TOKEN / AURA_VERIFY_SPACE_ID not given")
        status, body, _ = _request("GET", f"{self.hf_api}/api/spaces/{self.space_id}/runtime", token=self.hf_token)
        assert status == 200 and isinstance(body, dict), f"runtime: HTTP {status} {body}"
        hardware = (body.get("hardware") or {}).get("current")
        sleep = body.get("gcTimeout")
        assert body.get("stage") == "RUNNING", f"stage is {body.get('stage')!r}, not RUNNING"
        assert hardware == self.hardware, f"hardware is {hardware!r}, expected {self.hardware!r}"
        assert sleep is None or sleep <= 0, f"the Space sleeps after {sleep}s idle; it must never sleep"
        return f"stage=RUNNING hardware={hardware} sleep={sleep}"

    def c3_gateway_probes_it_healthy(self) -> str:
        status, body, _ = _request("GET", f"{self.gateway}/health/ready")
        assert status == 200 and isinstance(body, dict), f"/health/ready: HTTP {status} {body}"
        entry = (body.get("providers") or {}).get(self.provider) or {}
        assert entry, f"/health/ready lists no {self.provider!r} provider"
        assert entry.get("probe") != "passive", (
            f"{self.provider} is reported passive (from the circuit breaker, without calling it): set "
            f"AURA_PROVIDER_{self.provider.upper()}_HEALTH_PROBE=active now that the Space never sleeps")
        assert entry.get("healthy") is True, f"{self.provider} is unhealthy: {entry}"
        assert body.get("healthy") is True, "the gateway itself is not ready"
        schema = (body.get("checks") or {}).get("schema") or {}
        assert schema.get("healthy") is True, f"schema: {schema}"
        return f"{self.provider}={entry} schema={schema.get('revision')}"

    def c4_operator_surfaces_private(self) -> str:
        seen = {}
        for path in ("/metrics", "/docs", "/redoc", "/openapi.json"):
            status, _, _ = _request("GET", f"{self.gateway}{path}")
            seen[path] = status
        public = {p: s for p, s in seen.items() if s not in (401, 403, 404)}
        assert not public, f"answered without credentials: {public}"
        return f"{seen}"

    def c5_no_training_model(self) -> str:
        if not self.admin:
            raise _Skipped("AURA_VERIFY_ADMIN_TOKEN not given")
        status, body, _ = _request("GET", f"{self.gateway}/api/v3/admin/status", token=self.admin)
        assert status == 200 and isinstance(body, dict), f"/api/v3/admin/status: HTTP {status}"
        providers = body.get("providers") or []
        assert providers, "admin status lists no providers"
        training = [p.get("provider") for p in providers if p.get("may_train_on_prompts")]
        assert not training, f"may train on prompts: {training}"
        return f"{len(providers)} providers, none may train on prompts: {[p.get('provider') for p in providers]}"

    def c6_document_turn_on_gpu(self) -> str:
        if not self.token:
            raise _Skipped("AURA_VERIFY_TOKEN not given")
        code = f"{random.SystemRandom().randint(100000, 999999)}"
        status, body, _ = _request("POST", f"{self.gateway}/api/v3/knowledge/documents", token=self.token, body={
            "title": "AURA readiness check (deleted after the check)",
            "content": f"The AURA readiness verification code is {code} . Quote it exactly when asked.",
        })
        assert status == 200 and isinstance(body, dict) and body.get("document_id"), f"ingest: HTTP {status} {body}"
        document = body["document_id"]
        outcome: list[str] = []
        try:
            status, answer, took = _request("POST", f"{self.gateway}/api/v3/chat", token=self.token, body={
                "message": "What is the AURA readiness verification code in my documents? Answer with the number.",
                "use_knowledge": True, "use_memory": False, "use_tools": False, "plan": False,
                "max_output_tokens": 60,
            })
            assert status == 200 and isinstance(answer, dict), f"chat: HTTP {status} {answer}"
            provider = answer.get("provider")
            assert provider == self.provider, (
                f"answered by {provider!r}, expected {self.provider!r}: a document turn must stay on the GPU source")
            assert code in str(answer.get("message", "")), f"the answer lacks the code: {str(answer.get('message'))[:200]}"
            outcome.append(f"answered by {provider} in {took:.1f}s with the document's code")
        finally:
            # A test document must not stay in a real person's knowledge base (review G#2). A failed
            # cleanup fails the check, after any failure of the check's own.
            try:
                status, _, _ = _request("DELETE", f"{self.gateway}/api/v3/knowledge/documents/{document}", token=self.token)
            except Exception as exc:  # the check's own error, if any, stays the one reported
                status = f"{type(exc).__name__}"
            if status != 200:
                outcome.append(f"left behind: {document} (DELETE answered {status}); delete it by hand")
        assert not any(line.startswith("left behind") for line in outcome), "\n".join(outcome)
        return "\n".join(outcome)

    def c7_capacity(self) -> str:
        if not self.token:
            raise _Skipped("AURA_VERIFY_TOKEN not given")
        n = self.concurrency

        def turn(i: int) -> tuple[int, float, object]:
            status, body, took = _request("POST", f"{self.gateway}/api/v3/chat", token=self.tokens[i % len(self.tokens)], body={
                "message": f"Reply with the single word ready. ({i})",
                "use_knowledge": False, "use_memory": False, "use_tools": False, "plan": False,
                "max_output_tokens": 120,
            })
            return status, took, (body.get("provider") if isinstance(body, dict) else None)

        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(turn, range(n)))
        failed = [s for s, _, _ in results if s != 200]
        times = sorted(t for _, t, _ in results)
        p95 = percentile_95(times)
        providers = sorted({str(p) for _, _, p in results})
        summary = (f"{n} concurrent: p50={statistics.median(times):.1f}s p95={p95:.1f}s max={times[-1]:.1f}s "
                   f"providers={providers}")
        limited = failed.count(429)
        hint = (f"; {limited} were rate-limited (429): the gateway's per-user limit, not the GPU -- "
                "pass more users' tokens in AURA_VERIFY_EXTRA_TOKENS") if limited else ""
        assert not failed, f"{len(failed)} of {n} failed ({sorted(set(failed))}){hint}; {summary}"
        assert p95 <= self.p95_limit, f"p95 {p95:.1f}s exceeds {self.p95_limit:.0f}s; {summary}"
        return summary

    # -- run --------------------------------------------------------------------------------

    def run(self) -> int:
        self._out(f"AURA readiness: GPU source (SaaS P1-G), {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        self._out(f"gateway={self.gateway} space={self.space or '-'} space_id={self.space_id or '-'} "
                  f"expect_hardware={self.hardware} expect_provider={self.provider} "
                  f"concurrency={self.concurrency} p95_limit={self.p95_limit:.0f}s tokens={len(self.tokens)}")
        self.run_check(1, "The Space refuses unauthenticated calls and serves a model", self.c1_space_authenticated)
        self.run_check(2, f"The Space runs on {self.hardware} and never sleeps", self.c2_always_on_gpu)
        self.run_check(3, f"The gateway probes {self.provider} and reports it healthy", self.c3_gateway_probes_it_healthy)
        self.run_check(4, "/metrics, /docs, /redoc, /openapi.json are not public", self.c4_operator_surfaces_private)
        self.run_check(5, "No configured provider may train on prompts", self.c5_no_training_model)
        self.run_check(6, f"A document turn is answered from the document by {self.provider}", self.c6_document_turn_on_gpu)
        self.run_check(7, f"{self.concurrency} concurrent turns all succeed within the p95 limit", self.c7_capacity)
        failed = self.results.count("FAIL")
        skipped = self.results.count("SKIP")
        verdict = "PROVEN" if failed == 0 and skipped == 0 else "NOT PROVEN"
        self._out(f"summary: {self.results.count('PASS')} passed, {failed} failed, {skipped} skipped -- {verdict}")
        return failed


def main() -> int:
    global REPORT_DIR_USED
    verifier = Verifier(dict(os.environ))
    if not verifier.gateway:
        print("AURA_VERIFY_GATEWAY is required", file=sys.stderr)
        return 125
    failed = verifier.run()
    directory = Path(os.environ.get("AURA_VERIFY_REPORT_DIR", "docs/reports"))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"readiness-gpu-{stamp}.txt"
    while path.exists():  # two runs in one second: never overwrite evidence
        stamp += "x"
        path = directory / f"readiness-gpu-{stamp}.txt"
    path.write_text("\n".join(verifier.lines) + "\n")
    REPORT_DIR_USED = str(directory)
    print(f"report: {path}")
    return min(failed, 125)


if __name__ == "__main__":
    sys.exit(main())
