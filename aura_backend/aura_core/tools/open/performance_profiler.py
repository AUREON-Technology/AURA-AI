"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Its ``eval``/``exec`` of
code strings was removed; ``profile_python`` in ``tools/desktop_ops.py`` profiles on the user's paired
desktop instead, through ``aura_hostd``'s command policy.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import cProfile
import inspect
import io
import pstats
import time
import tracemalloc
from typing import Any, Callable, Dict, List, Optional


def _run_coroutine(coro: Any) -> Any:
    """Run a coroutine to completion on a fresh event loop.

    The coroutine used to be advanced once with ``send(None)`` and then handed to a loop, which
    resumed a half-run coroutine: anything that actually awaited (I/O, ``asyncio.sleep``) lost a
    step or hung. A new loop from the start is the whole fix.
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _invoke(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke a callable, resolving coroutines if needed.

    Security fix (v2): eval() and exec() have been removed.  The function now
    only accepts Python callables.  Passing a string raises TypeError with a
    clear message so callers that relied on the old code-string path get an
    actionable error rather than silent arbitrary code execution.
    """
    if not callable(func):
        raise TypeError(
            f"Expected a Python callable, got {type(func).__name__!r}.  "
            "Code strings are no longer accepted; wrap the code in a function "
            "and pass the function object instead."
        )
    target = func

    if not callable(func):
        raise TypeError(f"Expected a callable, got {type(func).__name__}")

    res = func(*args, **kwargs)
    if inspect.iscoroutine(res):
        return _run_coroutine(res)
    return res


class PerformanceProfiler:
    """Profiler for measuring execution latency, peak memory usage, and CPU hotspots.

    Security fix (v2): all methods now require Python callables; code strings
    are no longer accepted (eval/exec removed from _invoke).
    """

    @staticmethod
    def profile_callable(
        func: Any,
        *args: Any,
        measure_memory: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Measure wall-clock execution time and peak heap memory of a callable or code string.

        Timing is measured cleanly without tracemalloc overhead. Pre-existing tracemalloc
        sessions are preserved and not terminated.
        """
        was_tracing = tracemalloc.is_tracing()

        # 1. Clean timing pass without tracemalloc overhead
        result: Any = None
        success = False
        error: Optional[str] = None
        start_time = time.perf_counter()

        try:
            result = _invoke(func, *args, **kwargs)
            success = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 4)

        # 2. Memory pass (if requested, success, and tracemalloc can trace)
        current_bytes, peak_bytes = 0, 0
        if measure_memory and success:
            if not was_tracing:
                tracemalloc.start()
            else:
                try:
                    tracemalloc.reset_peak()
                except Exception:
                    pass

            try:
                _invoke(func, *args, **kwargs)
            except Exception:
                pass

            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            if not was_tracing:
                tracemalloc.stop()

        return {
            "success": success,
            "execution_time_ms": elapsed_ms,
            "current_memory_kb": round(current_bytes / 1024.0, 2),
            "memory_peak_kb": round(peak_bytes / 1024.0, 2),
            "result": result if success else None,
            "error": error,
        }

    @staticmethod
    def benchmark_callable(
        func: Any,
        *args: Any,
        iterations: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run a callable multiple times and report min, avg, max, and total latency in ms.

        Positional arguments after func are forwarded directly to the callable.
        iterations is keyword-only to avoid swallowing target arguments.
        """
        runs = max(1, iterations)
        durations_ms: List[float] = []
        error: Optional[str] = None

        for _ in range(runs):
            t0 = time.perf_counter()
            try:
                _invoke(func, *args, **kwargs)
                durations_ms.append((time.perf_counter() - t0) * 1000.0)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                break

        if error is not None:
            return {
                "success": False,
                "error": error,
                "iterations_completed": len(durations_ms),
                "iterations_requested": runs,
            }

        return {
            "success": True,
            "error": None,
            "iterations": runs,
            "min_ms": round(min(durations_ms), 4) if durations_ms else 0.0,
            "avg_ms": round(sum(durations_ms) / runs, 4) if durations_ms else 0.0,
            "max_ms": round(max(durations_ms), 4) if durations_ms else 0.0,
            "total_ms": round(sum(durations_ms), 4) if durations_ms else 0.0,
        }

    @staticmethod
    def profile_cprofile_top_functions(
        func: Any,
        *args: Any,
        top_n: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Profile a callable with cProfile and return a formatted summary of top time consumers.

        top_n is keyword-only to avoid swallowing positional arguments to func.
        """
        profiler = cProfile.Profile()
        profiler.enable()
        error: Optional[str] = None
        try:
            _invoke(func, *args, **kwargs)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            profiler.disable()

        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream).sort_stats("tottime")
        stats.print_stats(top_n)

        return {
            "success": error is None,
            "error": error,
            "top_n": top_n,
            "profile_report": stream.getvalue().strip(),
        }

    @classmethod
    def check_latency_budget(
        cls,
        func: Any,
        *args: Any,
        max_allowed_ms: float = 100.0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Verify whether a callable executes within a specified millisecond budget.

        max_allowed_ms is keyword-only so positional arguments are forwarded cleanly.
        Runs without tracemalloc overhead so latency checks are accurate.
        """
        prof = cls.profile_callable(func, *args, measure_memory=False, **kwargs)
        within_budget = prof["success"] and prof["execution_time_ms"] <= max_allowed_ms
        return {
            "within_budget": within_budget,
            "max_allowed_ms": max_allowed_ms,
            "actual_ms": prof["execution_time_ms"],
            "memory_peak_kb": prof["memory_peak_kb"],
            "success": prof["success"],
            "error": prof["error"],
        }
