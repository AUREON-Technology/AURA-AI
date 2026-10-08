"""Scrape the gateway's Prometheus ``/metrics`` before, during and after each step.

Tolerant by design: production nginx denies ``/metrics`` on the public edge (it leaks provider,
model and traffic data), so the scrape only works when the harness is pointed at the gateway
from inside its network (``--metrics-url http://gateway:8000/metrics``). An unreachable endpoint
is recorded as "not scraped", never as a zero.

A family can also be *declared* (``# TYPE`` line present) without a single series -- a labelled
gauge nobody ever sets. That is reported as "declared, never set", distinct from both "exported"
and "absent", because it means the signal looks available on a dashboard and is not.

Parses the text exposition format with the stdlib rather than depending on
``prometheus_client`` -- the load host needs nothing but ``httpx``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+(\S+)(?:\s+\d+)?\s*$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_TYPE = re.compile(r"^#\s*TYPE\s+([a-zA-Z_:][a-zA-Z0-9_:]*)\s+\w+")

#: Families summarised in the report, when present. Anything else scraped is kept in the JSON.
INTERESTING = re.compile(
    r"^aura_(turns_in_flight|llm_queue_depth|llm_inflight|llm_requests_total|llm_ttft_seconds|"
    r"llm_duration_seconds|turn_duration_seconds|turns_total|stage_duration_seconds|"
    r"rate_limit_rejections_total|llm_fallbacks_total|provider_circuit_opens_total|"
    r"provider_healthy|budget_exhausted_total|dependency_up|gpu_utilisation_ratio|"
    r"gpu_memory_used_bytes)"
    r"|pool|celery|queue",
)

#: Server-side signals Phase 3 asks for. Checked against every scrape so the report says
#: "not exported" / "declared, never set" instead of leaving a silent gap.
WANTED_SIGNALS: Mapping[str, str] = {
    "LLM queue wait (aura_llm_queue_depth)": r"^aura_llm_queue_depth$",
    "LLM in-flight generations (aura_llm_inflight)": r"^aura_llm_inflight$",
    "Turns in flight (aura_turns_in_flight)": r"^aura_turns_in_flight$",
    "Server-side TTFT (aura_llm_ttft_seconds)": r"^aura_llm_ttft_seconds(_sum|_count)?$",
    "DB connection pool": r"pool",
    "Celery queue depth": r"celery",
}

EXPORTED = "yes"
DECLARED_ONLY = "declared, never set"
ABSENT = "no"


def parse(text: str) -> dict[str, float]:
    """Exposition text -> ``{"name{a=\\"1\\",b=\\"2\\"}": value}`` with labels sorted."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        name, _, labels, value = match.groups()
        try:
            number = float(value)
        except ValueError:
            continue
        out[series_key(name, dict(_LABEL.findall(labels or "")))] = number
    return out


def declared(text: str) -> set[str]:
    """Family names announced by ``# TYPE`` lines, whether or not any series follows."""
    return {m.group(1) for m in map(_TYPE.match, text.splitlines()) if m}


def series_key(name: str, labels: Mapping[str, str]) -> str:
    if not labels:
        return name
    inner = ",".join(f'{k}="{labels[k]}"' for k in sorted(labels))
    return f"{name}{{{inner}}}"


def family(key: str) -> str:
    return key.split("{", 1)[0]


def total(snapshot: Mapping[str, float], name: str) -> float | None:
    """Sum every series of one metric name (across labels); ``None`` if absent."""
    values = [v for k, v in snapshot.items() if family(k) == name]
    return sum(values) if values else None


def signal_status(series_names: set[str], declared_names: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for signal, pattern in WANTED_SIGNALS.items():
        if any(re.search(pattern, n) for n in series_names):
            out[signal] = EXPORTED
        elif any(re.search(pattern, n) for n in declared_names):
            out[signal] = DECLARED_ONLY
        else:
            out[signal] = ABSENT
    return out


@dataclass
class StepMetrics:
    """Everything scraped for one matrix step."""

    reachable: bool = False
    error: str | None = None
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)
    #: (seconds since step start, queue depth, turns in flight, LLM in-flight) -- each summed
    #: over labels, ``None`` when the gateway reported no series for it.
    series: list[tuple[float, float | None, float | None, float | None]] = field(default_factory=list)

    def queue_series(self, *, start_s: float = 0.0) -> list[float] | None:
        if not self.reachable:
            return None
        values = [q for t, q, _, _ in self.series if q is not None and t >= start_s]
        return values or None

    def queue_note(self) -> str:
        if not self.reachable:
            return f"/metrics not scraped ({self.error or 'no --metrics-url'})"
        if "aura_llm_queue_depth" in self.declared:
            return "aura_llm_queue_depth is declared but has no series (never set by the gateway)"
        return "gateway exports no aura_llm_queue_depth"

    def deltas(self) -> dict[str, float]:
        """Counter/histogram increments over the step, for the interesting families."""
        out: dict[str, float] = {}
        for key, after in self.after.items():
            name = family(key)
            if not INTERESTING.search(name):
                continue
            if name.endswith(("_total", "_sum", "_count")):
                delta = after - self.before.get(key, 0.0)
                if delta:
                    out[key] = delta
        return out

    def summary(self) -> dict[str, Any]:
        """The handful of derived server-side numbers the report prints."""
        if not self.reachable:
            return {"reachable": False, "error": self.error}
        d = self.deltas()

        def mean_of(name: str) -> float | None:
            s = sum(v for k, v in d.items() if family(k) == f"{name}_sum")
            c = sum(v for k, v in d.items() if family(k) == f"{name}_count")
            return (s / c) if c else None

        def column(index: int) -> list[float]:
            return [row[index] for row in self.series if row[index] is not None]

        queue, in_flight, llm_inflight = column(1), column(2), column(3)
        series_names = {family(k) for k in (*self.before, *self.after)}
        stage_means: dict[str, float] = {}
        for key, value in d.items():
            if family(key) == "aura_stage_duration_seconds_sum":
                count = d.get(key.replace("_sum{", "_count{", 1))
                if count:
                    stage = re.search(r'stage="([^"]*)"', key)
                    stage_means[stage.group(1) if stage else key] = value / count * 1000.0
        llm_outcomes: dict[str, float] = {}
        for key, value in d.items():
            if family(key) == "aura_llm_requests_total":
                outcome = re.search(r'outcome="([^"]*)"', key)
                label = outcome.group(1) if outcome else "?"
                llm_outcomes[label] = llm_outcomes.get(label, 0.0) + value
        return {
            "reachable": True,
            "exported": signal_status(series_names, self.declared),
            "queue_depth_max": max(queue) if queue else None,
            "queue_depth_mean": (sum(queue) / len(queue)) if queue else None,
            "turns_in_flight_max": max(in_flight) if in_flight else None,
            "llm_inflight_max": max(llm_inflight) if llm_inflight else None,
            "server_ttft_mean_ms": _ms(mean_of("aura_llm_ttft_seconds")),
            "server_turn_mean_ms": _ms(mean_of("aura_turn_duration_seconds")),
            "stage_mean_ms": dict(sorted(stage_means.items())),
            "llm_requests_by_outcome": llm_outcomes,
            "rate_limit_rejections": _sum_family(d, "aura_rate_limit_rejections_total"),
            "fallbacks": _sum_family(d, "aura_llm_fallbacks_total"),
            "circuit_opens": _sum_family(d, "aura_provider_circuit_opens_total"),
        }


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else seconds * 1000.0


def _sum_family(d: Mapping[str, float], name: str) -> float | None:
    values = [v for k, v in d.items() if family(k) == name]
    return sum(values) if values else None


def compact(snapshot: Mapping[str, float]) -> dict[str, float]:
    """Keep only the interesting families (the raw exposition can be thousands of series)."""
    return {k: v for k, v in snapshot.items() if INTERESTING.search(family(k))}


def gauges_now(snapshot: Mapping[str, float]) -> tuple[float | None, float | None, float | None]:
    return (
        total(snapshot, "aura_llm_queue_depth"),
        total(snapshot, "aura_turns_in_flight"),
        total(snapshot, "aura_llm_inflight"),
    )


def names(snapshot: Iterable[str]) -> set[str]:
    return {family(k) for k in snapshot}
