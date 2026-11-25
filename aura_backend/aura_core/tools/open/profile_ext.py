"""Memory figures and baseline comparison for ``profile_python`` (ADR-046, row 23).

``profile_python`` runs a snippet under cProfile on the user's paired desktop and returns the hottest
functions. It reported no memory and could not say whether a run was *worse than before*. Two parts:

* ``PROFILE_WRAPPER`` -- the script the desktop runs. As before it prints the cProfile table, and it
  now also runs :mod:`tracemalloc` and prints one machine-readable line,
  ``AURA_PROFILE {"wall_s":..,"peak_kb":..,"current_kb":..,"top_allocations":[..]}``.
* ``parse_profile`` / ``compare`` -- run on the *gateway* over the text the desktop returned: pull
  that line out of the output and compare it with a ``baseline`` the caller passes (its own earlier
  figures; nothing is stored here), flagging a time or peak-memory regression beyond a tolerance.

Two honest limits. Timing under cProfile *and* tracemalloc is slower than a plain run (both hook every
call/allocation), so the figures are for comparing like with like, not absolute speed; and one
wall-clock sample is noisy, so the default tolerance is wide and the verdict names the numbers.
Server-side execution is deliberately not offered: this runs user code, which belongs on the user's
machine (ADR-036 group C).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

__all__ = ["PROFILE_WRAPPER", "parse_profile", "compare", "MARKER"]

MARKER = "AURA_PROFILE "

#: argv: 1 = source, 2 = top-N functions, 3 = "0" to switch memory tracking (tracemalloc) off.
PROFILE_WRAPPER = (
    "import cProfile, io, json, pstats, sys, time\n"
    "src, top = sys.argv[1], int(sys.argv[2])\n"
    "track = not (len(sys.argv) > 3 and sys.argv[3] == '0')\n"
    "if track:\n"
    "    import tracemalloc\n"
    "    tracemalloc.start(8)\n"
    "prof = cProfile.Profile()\n"
    "ns = {'__name__': '__main__'}\n"
    "t0 = time.perf_counter()\n"
    "prof.enable()\n"
    "try:\n"
    "    exec(compile(src, '<snippet>', 'exec'), ns)\n"
    "finally:\n"
    "    prof.disable()\n"
    "    wall = time.perf_counter() - t0\n"
    "    # Printed even when the snippet raised or called sys.exit(): a profile of a run that failed\n"
    "    # is still the profile the caller asked for, and the error still reaches stderr afterwards.\n"
    "    buf = io.StringIO()\n"
    "    st = pstats.Stats(prof, stream=buf)\n"
    "    st.sort_stats('tottime').print_stats(top)\n"
    "    print(buf.getvalue(), flush=True)\n"
    "    info = {'wall_s': round(wall, 6), 'function_calls': st.total_calls,\n"
    "            'profiled_s': round(st.total_tt, 6)}\n"
    "    if track:\n"
    "        cur, peak = tracemalloc.get_traced_memory()\n"
    "        snap = tracemalloc.take_snapshot().filter_traces([\n"
    "            tracemalloc.Filter(False, tracemalloc.__file__),\n"
    "            tracemalloc.Filter(False, '<frozen importlib._bootstrap>'),\n"
    "            tracemalloc.Filter(False, '<frozen importlib._bootstrap_external>')])\n"
    "        info.update(peak_kb=round(peak / 1024, 1), current_kb=round(cur / 1024, 1),\n"
    "                    top_allocations=[{'where': str(s.traceback[0]), 'kb': round(s.size / 1024, 1)}\n"
    "                                     for s in snap.statistics('lineno')[:5]])\n"
    "    print('AURA_PROFILE ' + json.dumps(info), flush=True)\n"
)

_LINE = re.compile(r"^AURA_PROFILE (\{.*\})\s*$", re.MULTILINE)
#: figures a baseline may carry, and whether a rise is bad (all of them: lower is better)
_METRICS = ("wall_s", "peak_kb", "function_calls", "profiled_s")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def parse_profile(output: str) -> dict[str, Any] | None:
    """The figures line the wrapper printed, or ``None`` (snippet killed early / old daemon)."""
    matches = _LINE.findall(output or "")
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
    except ValueError:
        return None
    clean: dict[str, Any] = {}
    for key in (*_METRICS, "current_kb"):
        number = _num(data.get(key))
        if number is not None:
            clean[key] = number
    if isinstance(data.get("top_allocations"), list):
        clean["top_allocations"] = [
            {"where": str(a.get("where", ""))[:200], "kb": _num(a.get("kb"))}
            for a in data["top_allocations"][:5] if isinstance(a, dict)
        ]
    return clean or None


def compare(
    metrics: dict[str, Any], baseline: dict[str, Any], max_regression_pct: float = 25.0,
    max_peak_kb: float | None = None,
) -> dict[str, Any]:
    """Each baseline figure against the current one: ``regressed`` when any rose by more than the tolerance."""
    checks: list[dict[str, Any]] = []
    unknown = [k for k in baseline if k not in _METRICS]
    for key in _METRICS:
        before = _num(baseline.get(key))
        now = _num(metrics.get(key))
        if before is None:
            continue
        if now is None:
            checks.append({"metric": key, "baseline": before, "current": None, "ok": None,
                           "note": "not measured in this run" + (" (memory tracking off)" if key == "peak_kb" else "")})
            continue
        change = ((now - before) / before * 100.0) if before > 0 else (0.0 if now == 0 else math.inf)
        checks.append({"metric": key, "baseline": before, "current": now,
                       "change_pct": round(change, 2) if math.isfinite(change) else "inf",
                       "limit_pct": max_regression_pct, "ok": change <= max_regression_pct})
    if max_peak_kb is not None:
        peak = _num(metrics.get("peak_kb"))
        checks.append({"metric": "peak_kb_budget", "budget": max_peak_kb, "current": peak,
                       "ok": None if peak is None else peak <= max_peak_kb})
    judged = [c for c in checks if c.get("ok") is not None]
    return {"regressed": any(c["ok"] is False for c in judged), "checks": checks,
            "compared": len(judged), "unknown_baseline_keys": unknown,
            "note": "single-sample wall-clock under cProfile+tracemalloc is noisy; repeat the run before "
                    "treating a small time regression as real"}
