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
from typing import Any, Dict, List, Optional


def _run_coroutine(coro: Any) -> Any:
    """Run a coroutine to completion on a fresh event loop.

    The coroutine used to be advanced once with ``send(None)`` and then handed to a loop, which
    resumed a half-run coroutine: anything that actually awaited (I/O, ``asyncio.sleep``) lost a
    step or hung. A new loop from the start is the whole fix.

    When the caller is itself inside a running event loop (an agent turn is), ``asyncio.run`` on this
    thread would raise "cannot be called from a running event loop"; the coroutine then runs on a
    worker thread with its own loop instead.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _await(awaitable: Any) -> Any:
    return await awaitable


def _invoke(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke a callable, resolving coroutines if needed.

    ``eval()`` and ``exec()`` are gone: only Python callables are accepted. Passing a string
    raises TypeError with a clear message so callers that relied on the old code-string path get an
    actionable error rather than silent arbitrary code execution.
    """
    if not callable(func):
        raise TypeError(
            f"Expected a Python callable, got {type(func).__name__!r}.  "
            "Code strings are no longer accepted; wrap the code in a function "
            "and pass the function object instead."
        )

    res = func(*args, **kwargs)
    if inspect.iscoroutine(res):
        return _run_coroutine(res)
    if inspect.isawaitable(res):
        return _run_coroutine(_await(res))
    return res


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


class PerformanceProfiler:
    """Profiler for measuring execution latency, peak memory usage, and CPU hotspots.

    Only Python callables are accepted (no code strings; ``eval``/``exec`` are gone). Reads no files,
    so there is no PDF handling to do.

    **A profiled callable really runs**, in this process, with this process's privileges. Profiling is
    not sandboxing: do not point it at code you would not run directly. There is no way to interrupt a
    callable that never returns, so a hung callable hangs the caller (use a subprocess for untrusted
    or unbounded code). ``profile_callable`` runs the callable **twice** by default (a clean timing
    pass, then a tracemalloc pass); the result's ``executions`` says how many times it ran, and
    ``single_run=True`` measures both in one traced run for callables with side effects.
    """

    @staticmethod
    def profile_callable(
        func: Any,
        *args: Any,
        measure_memory: bool = True,
        single_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Measure wall-clock execution time and peak heap memory of a callable.

        Timing is measured cleanly without tracemalloc overhead (unless ``single_run``, where the one
        run is traced and the time includes tracemalloc's overhead). Pre-existing tracemalloc sessions
        are preserved and not terminated.
        """
        was_tracing = tracemalloc.is_tracing()
        trace_first = bool(single_run and measure_memory)
        started_tracing = False
        if trace_first and not was_tracing:
            tracemalloc.start()
            started_tracing = True
        elif trace_first:
            try:
                tracemalloc.reset_peak()
            except Exception:
                pass

        # 1. Timing pass (clean, unless this is the single traced run)
        result: Any = None
        success = False
        error: Optional[str] = None
        executions = 1
        current_bytes, peak_bytes = 0, 0
        memory_measured = False
        memory_error: Optional[str] = None
        start_time = time.perf_counter()

        try:
            try:
                result = _invoke(func, *args, **kwargs)
                success = True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 4)

            if trace_first:
                current_bytes, peak_bytes = tracemalloc.get_traced_memory()
                memory_measured = True

            # 2. Memory pass: a second run, only if requested, the first succeeded, and not single_run
            elif measure_memory and success:
                if not was_tracing:
                    tracemalloc.start()
                    started_tracing = True
                else:
                    try:
                        tracemalloc.reset_peak()
                    except Exception:
                        pass
                executions = 2
                try:
                    _invoke(func, *args, **kwargs)
                    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
                    memory_measured = True
                except Exception as exc:
                    memory_error = f"second (memory) run failed: {type(exc).__name__}: {exc}"
        finally:
            if started_tracing and tracemalloc.is_tracing():
                tracemalloc.stop()

        out: Dict[str, Any] = {
            "success": success,
            "execution_time_ms": elapsed_ms,
            "current_memory_kb": round(current_bytes / 1024.0, 2),
            "memory_peak_kb": round(peak_bytes / 1024.0, 2),
            "memory_measured": memory_measured,
            "executions": executions,
            "result": result if success else None,
            "error": error,
        }
        if memory_error:
            out["memory_error"] = memory_error
        return out

    @staticmethod
    def benchmark_callable(
        func: Any,
        *args: Any,
        iterations: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run a callable multiple times and report min, avg, max, and total latency in ms.

        Positional arguments after func are forwarded directly to the callable.
        iterations is keyword-only to avoid swallowing target arguments, and is clamped to 1..10000.
        """
        runs = _bounded_int(iterations, 5, 1, 10_000)
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

        top_n is keyword-only to avoid swallowing positional arguments to func (clamped to 1..200).
        """
        top = _bounded_int(top_n, 10, 1, 200)
        profiler = cProfile.Profile()
        error: Optional[str] = None
        try:
            profiler.enable()
        except Exception as exc:  # another profiler already owns the interpreter hook
            return {"success": False, "error": f"Could not start cProfile: {type(exc).__name__}: {exc}",
                    "top_n": top, "profile_report": ""}
        try:
            _invoke(func, *args, **kwargs)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            profiler.disable()

        stream = io.StringIO()
        try:
            stats = pstats.Stats(profiler, stream=stream).sort_stats("tottime")
            stats.print_stats(top)
            report = stream.getvalue().strip()
        except Exception as exc:  # nothing was recorded
            report = ""
            error = error or f"No profile data was recorded ({type(exc).__name__}: {exc})"

        return {
            "success": error is None,
            "error": error,
            "top_n": top,
            "profile_report": report,
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

        max_allowed_ms is keyword-only so positional arguments are forwarded cleanly. It must be a
        positive finite number; anything else fails the check with an error (it never passes by
        default). Runs without tracemalloc overhead so latency checks are accurate.
        """
        if (
            isinstance(max_allowed_ms, bool)
            or not isinstance(max_allowed_ms, (int, float))
            or max_allowed_ms != max_allowed_ms
            or max_allowed_ms <= 0
        ):
            return {
                "within_budget": False,
                "max_allowed_ms": max_allowed_ms,
                "actual_ms": None,
                "memory_peak_kb": 0.0,
                "success": False,
                "error": "max_allowed_ms must be a positive number; the callable was not run.",
            }
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
