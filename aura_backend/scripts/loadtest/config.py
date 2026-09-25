"""Run configuration: the matrix, the scenarios, the traffic mix, and the launch thresholds.

Everything a run was configured with is written into its results file verbatim (minus secrets),
because a latency number without the concurrency, think time, output-token ceiling and timeout
that produced it is not a number anyone can compare against.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

#: The Phase 3 matrix. Configurable per run; this is only the default.
DEFAULT_MATRIX: tuple[int, ...] = (1, 2, 5, 10, 20, 30, 40, 50, 75, 100)

#: Scenario ids. ``mixed`` (F) draws from the others by weight rather than being one itself.
SCENARIOS: tuple[str, ...] = ("chat", "long_context", "agent", "engineer", "voice", "api", "mixed")

SCENARIO_TITLES: Mapping[str, str] = {
    "chat": "A - normal chat (short prompt + RAG)",
    "long_context": "B - long-context chat (long history + retrieved docs)",
    "agent": "C - agent (plan -> tool -> observe -> respond)",
    "engineer": "D - Engineer (repo read -> edit -> test, sandboxed)",
    "voice": "E - voice (STT -> kernel -> TTS)",
    "api": "non-LLM API (list/search/memory/agents)",
    "mixed": "F - mixed workload",
}

#: Default traffic distribution for ``mixed``. Weights, not percentages -- they are normalised
#: over whichever scenarios are actually available on the target (a skipped scenario's weight is
#: dropped and the drop is recorded in the report, never silently redistributed).
DEFAULT_MIX: Mapping[str, float] = {
    "chat": 50.0,
    "long_context": 10.0,
    "agent": 15.0,
    "voice": 5.0,
    "engineer": 0.0,
    "api": 20.0,
}


@dataclass(frozen=True)
class Thresholds:
    """The proposed launch thresholds. Each is evaluated per concurrency step.

    Which samples each one is evaluated over is part of the definition, stated once here and
    repeated in the report:

    - ``api_*``: requests of kind ``api`` -- knowledge list/search, memory list, agents list.
      Speech (STT/TTS) is inference, not a "non-LLM API", and is not in this population.
    - ``ttft_p95_ms`` and ``response_p95_ms``: scenario A (normal chat) only. Agent turns plan
      before they generate and long-context turns prefill a large prompt; both are reported
      but gating them on an interactive-chat budget would make the threshold meaningless.
    - ``server_error_rate``: every request. Counts 5xx, 503, client-side timeouts, dropped
      connections and mid-stream server errors -- a request that never answered is a server
      failure whether or not a status line made it back. 429s are *not* in it: they are the
      rate limiter doing its job and are reported separately.
    - ``bounded_queue``: server-side ``aura_llm_queue_depth`` sampled during the step. Not
      evaluated (rather than passed) when the gateway's /metrics is unreachable.
    """

    api_p95_ms: float = 500.0
    api_p99_ms: float = 1000.0
    ttft_p95_ms: float = 2500.0
    response_p95_ms: float = 15000.0
    server_error_rate: float = 0.01
    #: Absolute ceiling on the sampled queue depth; ``None`` applies only the growth test.
    max_queue_depth: float | None = None
    #: Queue still growing at the end of the step: last-third mean exceeds first-third mean by
    #: more than max(this, 50% of first-third mean).
    queue_growth_tolerance: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunConfig:
    base_url: str
    matrix: tuple[int, ...] = DEFAULT_MATRIX
    scenarios: tuple[str, ...] = ("mixed",)
    mix: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_MIX))
    #: Seconds over which a step's virtual users are started, one every ramp/concurrency.
    ramp_s: float = 10.0
    #: Steady-state measurement window after ramp-up. Ignored when ``iterations_per_vu`` is set.
    duration_s: float = 60.0
    #: Count-bound mode (smoke tests, quick checks): each VU runs exactly this many iterations.
    iterations_per_vu: int | None = None
    think_min_s: float = 1.0
    think_max_s: float = 3.0
    #: Pause between matrix steps so one step's tail does not bleed into the next's numbers.
    cooldown_s: float = 5.0
    max_output_tokens: int = 256
    request_timeout_s: float = 300.0
    api_timeout_s: float = 30.0
    connect_timeout_s: float = 10.0
    max_retries: int = 0
    retry_cap_s: float = 10.0
    seed: int = 1
    # auth
    auth_mode: str = "none"  # none | tokens | mint
    tokens_file: str | None = None
    jwt_secret_env: str = "AURA_LOADTEST_JWT_SECRET"
    mint_role: str = "User"
    user_prefix: str = "loadtest-vu"
    # scenario tuning
    long_context_chars: int = 12_000
    voice_wav: str | None = None
    engineer_repo: str | None = None
    engineer_test_command: str = "pytest -q"
    engineer_max_confirm_rounds: int = 3
    setup_documents: bool = True
    model_probe: bool = True
    # observability
    metrics_url: str | None = None
    metrics_interval_s: float = 5.0
    # run conditions, recorded verbatim
    label: str = "loadtest"
    target_sha: str | None = None
    target_hardware: str | None = None
    model_label: str | None = None
    conditions: Mapping[str, str] = field(default_factory=dict)
    ca_bundle: str | None = None
    include_raw_samples: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds)

    def validate(self) -> None:
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.matrix or any(c < 1 for c in self.matrix):
            raise ValueError("matrix must be a non-empty list of positive integers")
        unknown = [s for s in self.scenarios if s not in SCENARIOS]
        if unknown:
            raise ValueError(f"unknown scenario(s): {', '.join(unknown)}; known: {', '.join(SCENARIOS)}")
        bad_mix = [k for k in self.mix if k not in SCENARIOS or k == "mixed"]
        if bad_mix:
            raise ValueError(f"unknown scenario(s) in mix: {', '.join(bad_mix)}")
        if any(w < 0 for w in self.mix.values()):
            raise ValueError("mix weights must be non-negative")
        if self.think_min_s < 0 or self.think_max_s < self.think_min_s:
            raise ValueError("think time must satisfy 0 <= min <= max")
        if self.iterations_per_vu is not None and self.iterations_per_vu < 1:
            raise ValueError("iterations_per_vu must be >= 1")
        if self.iterations_per_vu is None and self.duration_s <= 0:
            raise ValueError("duration_s must be > 0 in time-bound mode")
        if self.auth_mode not in ("none", "tokens", "mint"):
            raise ValueError("auth_mode must be one of none|tokens|mint")
        if self.auth_mode == "tokens" and not self.tokens_file:
            raise ValueError("auth_mode=tokens needs tokens_file")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")

    def public_dict(self) -> dict[str, Any]:
        """What gets written to the results file: everything except anything secret-bearing.

        The JWT secret is never held on this object (only the *name* of the env var it is read
        from), and a tokens file is recorded by path, never by content.
        """
        from .redact import redact_url

        data = asdict(self)
        data["base_url"] = redact_url(self.base_url)
        if self.metrics_url:
            data["metrics_url"] = redact_url(self.metrics_url)
        data["matrix"] = list(self.matrix)
        data["scenarios"] = list(self.scenarios)
        data["mix"] = dict(self.mix)
        data["conditions"] = dict(self.conditions)
        return data


def parse_matrix(text: str) -> tuple[int, ...]:
    values = tuple(int(part) for part in text.replace(" ", "").split(",") if part)
    if not values or any(v < 1 for v in values):
        raise ValueError(f"invalid matrix {text!r}: expected e.g. 1,2,5,10")
    return tuple(sorted(set(values)))


def parse_mix(text: str) -> dict[str, float]:
    """``chat=50,agent=15,api=20`` -> weights. Unlisted scenarios get weight 0."""
    mix: dict[str, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, weight = part.partition("=")
        if not sep:
            raise ValueError(f"invalid mix entry {part!r}: expected name=weight")
        mix[name.strip()] = float(weight)
    return mix


def parse_range(text: str) -> tuple[float, float]:
    """``2`` -> (2, 2); ``1-3`` -> (1, 3)."""
    lo, sep, hi = text.partition("-")
    low = float(lo)
    high = float(hi) if sep else low
    return low, high


def parse_conditions(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or ():
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"invalid condition {item!r}: expected KEY=VALUE")
        out[key.strip()] = value.strip()
    return out
