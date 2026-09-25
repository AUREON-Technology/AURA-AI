"""The matrix runner: preflight, unmeasured setup, then one closed-loop step per concurrency.

A step starts ``concurrency`` virtual users spread evenly over ``ramp_s``; each loops "run one
scenario iteration, think, repeat" until the steady-state window ends (time-bound mode) or it has
run ``iterations_per_vu`` iterations (count-bound mode). Only requests *started* inside the
steady window count toward the step's numbers, so ramp-up and tail-drain do not dilute them.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import platform
import random
import subprocess
import time
import uuid
from typing import Any, Callable, Mapping

from . import HARNESS_VERSION, prom
from .auth import TokenSource
from .client import VU, Gateway
from .config import SCENARIO_TITLES, RunConfig
from .redact import redact_url
from .scenarios import (
    SCENARIO_FUNCS,
    Preflight,
    scenario_availability,
    setup_user_documents,
)
from .stats import (
    ERR_429,
    FAIL,
    PASS,
    Iteration,
    Sample,
    detect_knee,
    evaluate_thresholds,
    summarize,
)
from .wav import load_wav

Log = Callable[[str], None]

#: Scenarios that retrieve from the per-identity documents set up before the matrix.
_NEEDS_DOCUMENTS = {"chat", "long_context", "agent", "voice", "api", "mixed"}


class HarnessAbort(RuntimeError):
    """The target cannot be load-tested as configured (auth rejected, unreachable, ...)."""


def build_tokens(cfg: RunConfig) -> TokenSource:
    if cfg.auth_mode == "mint":
        # Long enough for the whole matrix plus margin; TokenSource re-mints near expiry anyway.
        return TokenSource.minted_from_env(
            cfg.jwt_secret_env, role=cfg.mint_role, user_prefix=cfg.user_prefix, ttl_s=3600,
        )
    if cfg.auth_mode == "tokens":
        return TokenSource.from_file(cfg.tokens_file or "")
    return TokenSource.none()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class Runner:
    def __init__(
        self,
        cfg: RunConfig,
        *,
        tokens: TokenSource | None = None,
        transport: Any = None,
        in_process: bool = False,
        log: Log | None = None,
    ) -> None:
        cfg.validate()
        self.cfg = cfg
        self.tokens = tokens or build_tokens(cfg)
        self.transport = transport
        self.in_process = in_process
        self.log = log or (lambda msg: print(msg, flush=True))
        self.run_id = uuid.uuid4().hex[:8]

    # -- entry point -------------------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        import httpx

        cfg = self.cfg
        peak = max(cfg.matrix)
        client_kwargs: dict[str, Any] = {
            "base_url": cfg.base_url,
            "timeout": httpx.Timeout(cfg.request_timeout_s, connect=cfg.connect_timeout_s),
            "limits": httpx.Limits(max_connections=peak * 2 + 10, max_keepalive_connections=peak + 10),
        }
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        else:
            client_kwargs["verify"] = cfg.ca_bundle or True
        started_at = _dt.datetime.now(_dt.timezone.utc)
        async with httpx.AsyncClient(**client_kwargs) as client:
            metrics_client = None
            if cfg.metrics_url and not cfg.metrics_url.startswith("/"):
                metrics_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0), verify=cfg.ca_bundle or True
                )
            try:
                gw = Gateway(client, cfg, self.tokens)
                self._scrape_client = metrics_client or client
                pre = await self._preflight(gw)
                voice_desc = None
                if any(pre.available.get(s, (False, ""))[0] for s in ("voice",)):
                    gw.voice_wav, voice_desc = load_wav(cfg.voice_wav)
                setup = await self._setup(gw, pre)
                runs = []
                for run_label in cfg.scenarios:
                    runs.append(await self._run_matrix(gw, pre, run_label))
            finally:
                if metrics_client is not None:
                    await metrics_client.aclose()
        finished_at = _dt.datetime.now(_dt.timezone.utc)
        return self._results(pre, setup, runs, started_at, finished_at, voice_desc)

    # -- preflight & setup -------------------------------------------------------------

    async def _preflight(self, gw: Gateway) -> Preflight:
        cfg = self.cfg
        pre = Preflight()
        vu0 = VU(index=0, rng=random.Random(cfg.seed), session_id=f"lt-{self.run_id}-pre", t0=time.perf_counter())
        try:
            response = await gw.client.get("/health/ready", timeout=cfg.api_timeout_s)
            pre.health_status = response.status_code
            if "json" in response.headers.get("content-type", ""):
                pre.health = response.json()
        except Exception as exc:
            self.log(f"preflight: /health/ready unreachable ({type(exc).__name__}); continuing")

        sample, result = await gw.request(
            vu0, scenario="preflight", op="agents", kind="api", method="GET", path="/api/v3/agents",
        )
        if not sample.ok:
            raise HarnessAbort(
                f"preflight GET /api/v3/agents failed ({sample.status}, {sample.error}: "
                f"{sample.detail}) -- check --base-url and the auth mode before loading anything"
            )
        pre.agents = list((result.body or {}).get("agents") or [])

        sample, result = await gw.request(
            vu0, scenario="preflight", op="workspaces", kind="api", method="GET",
            path="/api/v3/engineering/workspaces",
        )
        if sample.ok:
            pre.workspaces_enabled = bool((result.body or {}).get("enabled"))

        if cfg.model_probe:
            sample, result = await gw.request(
                vu0, scenario="preflight", op="model_probe", kind="llm", method="POST",
                path="/api/v3/chat", timeout_s=cfg.request_timeout_s,
                json_body={
                    "message": "Reply with the single word: ready.", "use_knowledge": False,
                    "use_memory": False, "use_tools": False, "max_output_tokens": 16,
                    "session_id": f"lt-{self.run_id}-probe",
                },
            )
            if sample.ok:
                pre.probe_provider = (result.body or {}).get("provider")
                pre.probe_model = (result.body or {}).get("model")
            else:
                pre.probe_error = f"{sample.status} {sample.error}: {sample.detail}"

        wanted = set(cfg.scenarios)
        if "mixed" in wanted:
            wanted |= {k for k, w in cfg.mix.items() if w > 0}
        for name in sorted(wanted - {"mixed"}):
            pre.available[name] = scenario_availability(name, cfg, pre)
        for name, (ok, reason) in sorted(pre.available.items()):
            if not ok:
                self.log(f"preflight: scenario {name} SKIPPED -- {reason}")
        return pre

    async def _setup(self, gw: Gateway, pre: Preflight) -> dict[str, Any]:
        cfg = self.cfg
        if not cfg.setup_documents or not (set(cfg.scenarios) & _NEEDS_DOCUMENTS):
            return {"identities": 0, "failed": 0, "skipped": True}
        distinct = self.tokens.distinct_users
        identities = max(cfg.matrix) if distinct is None else min(distinct, max(cfg.matrix))
        gate = asyncio.Semaphore(8)
        failures: list[str] = []

        async def one(index: int) -> None:
            async with gate:
                vu = VU(index=index, rng=random.Random(index), session_id=f"lt-{self.run_id}-setup",
                        t0=time.perf_counter())
                for sample in await setup_user_documents(gw, vu):
                    if not sample.ok:
                        failures.append(f"{sample.status} {sample.error}: {sample.detail}")

        started = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(identities)))
        elapsed = time.perf_counter() - started
        self.log(f"setup: documents for {identities} identities in {elapsed:.1f}s, {len(failures)} failures")
        if failures and len(failures) >= identities * 2:
            raise HarnessAbort(f"setup failed for every identity; first error: {failures[0]}")
        return {"identities": identities, "failed": len(failures), "seconds": round(elapsed, 2),
                "first_error": failures[0] if failures else None}

    # -- the matrix --------------------------------------------------------------------

    def _mix_for(self, run_label: str, pre: Preflight) -> tuple[dict[str, float], list[str]]:
        """Effective weights for a run, and the scenarios dropped from it (with reasons)."""
        if run_label != "mixed":
            return {run_label: 1.0}, []
        weights: dict[str, float] = {}
        dropped: list[str] = []
        for name, weight in self.cfg.mix.items():
            if weight <= 0:
                continue
            ok, reason = pre.available.get(name, (True, ""))
            if ok:
                weights[name] = weight
            else:
                dropped.append(f"{name} (weight {weight:g}): {reason}")
        return weights, dropped

    async def _run_matrix(self, gw: Gateway, pre: Preflight, run_label: str) -> dict[str, Any]:
        cfg = self.cfg
        weights, dropped = self._mix_for(run_label, pre)
        run: dict[str, Any] = {
            "run": run_label,
            "title": SCENARIO_TITLES.get(run_label, run_label),
            "mix": weights,
            "dropped_from_mix": dropped,
            "steps": [],
        }
        if run_label != "mixed":
            ok, reason = pre.available.get(run_label, (True, ""))
            if not ok:
                run["skipped"] = reason
                return run
        if not weights:
            run["skipped"] = "no available scenario has a positive weight in the mix"
            return run
        for position, concurrency in enumerate(cfg.matrix):
            step = await self._run_step(gw, run_label, weights, concurrency)
            run["steps"].append(step)
            s = step["summary"]
            ttft = s["ttft_ms"].get("p95")
            e2e = s["e2e_ms"].get("p95")
            self.log(
                f"{run_label:<13} c={concurrency:<4} req={s['requests']:<5} "
                f"rps={s['rps']:7.2f} ttft_p95={_ms(ttft):>9} e2e_p95={_ms(e2e):>9} "
                f"err={s['error_rate']:.1%} 5xx={s['server_failure_rate']:.1%}"
            )
            if position < len(cfg.matrix) - 1 and cfg.cooldown_s > 0:
                await asyncio.sleep(cfg.cooldown_s)
        points = [
            (st["concurrency"], st["summary"]["goodput_per_s"], st["summary"]["e2e_ms"].get("p95"))
            for st in run["steps"]
        ]
        knee = detect_knee(points)
        run["knee"] = knee.as_dict() if knee else None
        passing = [
            st["concurrency"] for st in run["steps"]
            if st["thresholds"] and all(t["verdict"] != FAIL for t in st["thresholds"])
            and any(t["verdict"] == PASS for t in st["thresholds"])
        ]
        # Highest step at or below which *every* step passed -- a pass above a failure is noise.
        highest = None
        for st in run["steps"]:
            if st["concurrency"] in passing:
                highest = st["concurrency"]
            else:
                break
        run["highest_passing_concurrency"] = highest
        return run

    async def _run_step(
        self, gw: Gateway, run_label: str, weights: Mapping[str, float], concurrency: int,
    ) -> dict[str, Any]:
        cfg = self.cfg
        gw.counters = {}
        count_mode = cfg.iterations_per_vu is not None
        ramp = cfg.ramp_s if concurrency > 1 else 0.0
        steady_start = ramp
        steady_end = ramp + cfg.duration_s
        names = list(weights)
        cumulative = list(weights.values())
        samples: list[Sample] = []
        iterations: list[Iteration] = []
        harness_errors: list[str] = []
        metrics = prom.StepMetrics()

        t0 = time.perf_counter()
        before = await self._scrape()
        if before is not None:
            metrics.reachable = True
            metrics.before = prom.compact(before)
        else:
            metrics.error = self._scrape_error
        stop_sampling = asyncio.Event()

        async def sampler() -> None:
            while not stop_sampling.is_set():
                snap = await self._scrape()
                if snap is not None:
                    metrics.series.append((round(time.perf_counter() - t0, 3), *prom.gauges_now(snap)))
                try:
                    await asyncio.wait_for(stop_sampling.wait(), timeout=cfg.metrics_interval_s)
                except asyncio.TimeoutError:
                    pass

        sampler_task = asyncio.create_task(sampler()) if metrics.reachable else None

        async def vu_loop(index: int) -> None:
            await asyncio.sleep(ramp * index / concurrency)
            vu = VU(
                index=index,
                rng=random.Random(cfg.seed * 1_000_003 + concurrency * 7_919 + index),
                session_id=f"lt-{self.run_id}-{run_label}-c{concurrency}-vu{index}",
                t0=t0,
            )
            done = 0
            while True:
                if count_mode:
                    if done >= (cfg.iterations_per_vu or 0):
                        break
                elif vu.now() >= steady_end:
                    break
                scenario = names[0] if len(names) == 1 else vu.rng.choices(names, weights=cumulative)[0]
                started = vu.now()
                began = time.perf_counter()
                try:
                    got = await SCENARIO_FUNCS[scenario](gw, vu)
                except Exception as exc:  # a harness defect, never a server verdict
                    harness_errors.append(f"{scenario}: {type(exc).__name__}: {exc}")
                    got = []
                samples.extend(got)
                llm = next((s for s in got if s.ttft_ms is not None), None)
                iterations.append(
                    Iteration(
                        scenario=scenario, vu=index, started_s=started,
                        e2e_ms=(time.perf_counter() - began) * 1000.0,
                        ok=bool(got) and all(s.ok for s in got), requests=len(got),
                        ttft_ms=llm.ttft_ms if llm else None,
                    )
                )
                done += 1
                think = vu.rng.uniform(cfg.think_min_s, cfg.think_max_s)
                if not count_mode and vu.now() + think >= steady_end:
                    break
                if think > 0:
                    await asyncio.sleep(think)

        await asyncio.gather(*(vu_loop(i) for i in range(concurrency)))
        wall = time.perf_counter() - t0
        if sampler_task is not None:
            stop_sampling.set()
            await sampler_task
        after = await self._scrape()
        if after is not None:
            metrics.after = prom.compact(after)
        metrics.declared = set(self._declared)

        if count_mode:
            window = wall
        else:
            window = cfg.duration_s
            for s in samples:
                s.steady = steady_start <= s.started_s < steady_end
            for it in iterations:
                it.steady = steady_start <= it.started_s < steady_end

        by_scenario = {
            name: summarize(
                [s for s in samples if s.scenario == name],
                [i for i in iterations if i.scenario == name],
                window_s=window,
            ).as_dict()
            for name in sorted({i.scenario for i in iterations})
        }
        by_op: dict[str, Any] = {}
        for key in sorted({(s.scenario, s.op, s.kind) for s in samples}):
            subset = [s for s in samples if (s.scenario, s.op, s.kind) == key]
            summary = summarize(subset, [], window_s=window)
            by_op[f"{key[0]}/{key[1]}"] = {
                "kind": key[2],
                "requests": summary.requests,
                "request_ms": summary.request_ms,
                "ttft_ms": summary.ttft_ms,
                "errors": {k: v for k, v in summary.errors.items() if v},
            }
        queue_start = steady_start if not count_mode else 0.0
        thresholds = evaluate_thresholds(
            samples, iterations, cfg.thresholds,
            queue_series=metrics.queue_series(start_s=queue_start),
            queue_note=metrics.queue_note(),
        )
        overall = summarize(samples, iterations, window_s=window)
        step: dict[str, Any] = {
            "concurrency": concurrency,
            "mode": "count" if count_mode else "time",
            "ramp_s": ramp,
            "window_s": round(window, 3),
            "wall_s": round(wall, 3),
            "summary": overall.as_dict(),
            "by_scenario": by_scenario,
            "by_op": by_op,
            "thresholds": [t.as_dict() for t in thresholds],
            "metrics": {**metrics.summary(), "series": metrics.series, "deltas": metrics.deltas()},
            "counters": dict(sorted(gw.counters.items())),
            "harness_errors": harness_errors[:20],
            "harness_error_count": len(harness_errors),
            "rate_limited_share": (
                overall.errors.get(ERR_429, 0) / overall.requests if overall.requests else 0.0
            ),
        }
        if cfg.include_raw_samples:
            step["samples"] = [_sample_row(s) for s in samples]
        return step

    # -- metrics -----------------------------------------------------------------------

    _scrape_error: str | None = "no --metrics-url"
    _declared: frozenset[str] = frozenset()

    async def _scrape(self) -> dict[str, float] | None:
        url = self.cfg.metrics_url
        if not url:
            return None
        try:
            response = await self._scrape_client.get(url, timeout=10.0)
        except Exception as exc:
            self._scrape_error = f"{type(exc).__name__}"
            return None
        if response.status_code != 200:
            self._scrape_error = f"HTTP {response.status_code}"
            return None
        self._declared = frozenset(self._declared | prom.declared(response.text))
        return prom.parse(response.text)

    # -- results -----------------------------------------------------------------------

    def _results(
        self, pre: Preflight, setup: Mapping[str, Any], runs: list[dict[str, Any]],
        started_at: _dt.datetime, finished_at: _dt.datetime, voice_desc: str | None,
    ) -> dict[str, Any]:
        cfg = self.cfg
        model = cfg.model_label or (
            f"{pre.probe_provider}/{pre.probe_model}" if pre.probe_provider else "UNSPECIFIED"
        )
        harness_sha = _git_sha()
        target_sha = cfg.target_sha or (
            f"{harness_sha} (in-process: the harness's own checkout)" if self.in_process
            else "not recorded (pass --target-sha)"
        )
        conditions = {
            "date_utc": started_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "finished_utc": finished_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "harness_version": HARNESS_VERSION,
            "harness_git_sha": harness_sha,
            "target_sha": target_sha,
            "gateway_url": redact_url(cfg.base_url),
            "metrics_url": redact_url(cfg.metrics_url) if cfg.metrics_url else None,
            "target_hardware": cfg.target_hardware or "UNSPECIFIED (pass --target-hardware)",
            "model": model,
            "probe_provider": pre.probe_provider,
            "probe_model": pre.probe_model,
            "environment": (pre.health or {}).get("environment"),
            "in_process": self.in_process,
            "auth": self.tokens.describe(),
            "voice_audio": voice_desc,
            "load_generator": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpus": os.cpu_count(),
            },
            "extra": dict(cfg.conditions),
        }
        return {
            "schema": "aura-loadtest/1",
            "run_id": self.run_id,
            "label": cfg.label,
            "conditions": conditions,
            "config": cfg.public_dict(),
            "thresholds": cfg.thresholds.as_dict(),
            "preflight": pre.as_dict(),
            "setup": dict(setup),
            "runs": runs,
            "warnings": _warnings(cfg, pre, runs, self.tokens, self.in_process, voice_desc),
        }


def _warnings(
    cfg: RunConfig, pre: Preflight, runs: list[dict[str, Any]], tokens: TokenSource,
    in_process: bool, voice_desc: str | None,
) -> list[str]:
    out: list[str] = []
    if (pre.probe_provider or "").lower() == "echo" or in_process:
        out.append(
            "ECHO PROVIDER / IN-PROCESS RUN: these numbers measure the harness and the pipeline "
            "around a model that answers instantly. They are NOT a capacity measurement."
        )
    if in_process:
        out.append(
            "In-process ASGI transport buffers each response, so TTFT here is ~ the full "
            "response time; real TTFT needs a real network stream."
        )
    if tokens.mode == "none":
        out.append("Auth mode `none`: every VU is the same user, so per-user state and limits collapse.")
    distinct = tokens.distinct_users
    if distinct is not None and distinct < max(cfg.matrix) and tokens.mode == "tokens":
        out.append(
            f"Only {distinct} identities for up to {max(cfg.matrix)} VUs: several VUs share a user "
            "and its per-user rate limit."
        )
    if not cfg.metrics_url:
        out.append("No --metrics-url: server-side queue/in-flight metrics were not captured, and the "
                   "bounded-queue threshold is NOT EVALUATED.")
    for run in runs:
        for step in run.get("steps", []):
            if step["rate_limited_share"] > 0.05:
                out.append(
                    f"{run['run']} at {step['concurrency']} users: {step['rate_limited_share']:.0%} "
                    "of requests were 429s -- this step measured the rate limiter, not capacity "
                    "(see the runbook on raising limits for a test window)."
                )
                break
        for step in run.get("steps", []):
            if step["harness_error_count"]:
                out.append(f"{run['run']} at {step['concurrency']} users: "
                           f"{step['harness_error_count']} harness errors (a harness defect, see JSON).")
                break
    if voice_desc and "synthesized" in voice_desc:
        out.append("Voice used the synthesized (non-speech) WAV; pass --voice-wav with a real "
                   "recording for representative STT cost.")
    return out


def _sample_row(s: Sample) -> dict[str, Any]:
    row = {
        "scenario": s.scenario, "op": s.op, "kind": s.kind, "vu": s.vu,
        "t": round(s.started_s, 3), "ms": round(s.e2e_ms, 1), "status": s.status,
        "steady": s.steady,
    }
    if s.ttft_ms is not None:
        row["ttft"] = round(s.ttft_ms, 1)
    if s.error:
        row["error"] = s.error
        row["detail"] = s.detail
    if s.retries:
        row["retries"] = s.retries
    if s.completion_tokens is not None:
        row["tokens"] = s.completion_tokens
    if s.tokens_per_s is not None:
        row["tps"] = round(s.tokens_per_s, 2)
    if s.steps:
        row["steps"] = s.steps
    return row


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}ms"
