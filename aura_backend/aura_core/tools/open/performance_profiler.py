import cProfile
import io
import pstats
import time
import tracemalloc
from typing import Any, Callable, Dict, Optional


class PerformanceProfiler:
    """Profiler for measuring execution latency, peak memory usage, and CPU hotspots."""

    @staticmethod
    def profile_callable(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Measure wall-clock execution time and peak heap memory of a callable."""
        tracemalloc.start()
        start_time = time.perf_counter()

        result: Any = None
        success = False
        error: Optional[str] = None

        try:
            result = func(*args, **kwargs)
            success = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = round((time.perf_counter() - start_time) * 1000.0, 4)
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
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
        func: Callable[..., Any],
        iterations: int = 5,
        *args: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run a callable multiple times and report min, avg, max, and total latency in ms."""
        runs = max(1, iterations)
        durations_ms: List[float] = []

        for _ in range(runs):
            t0 = time.perf_counter()
            func(*args, **kwargs)
            durations_ms.append((time.perf_counter() - t0) * 1000.0)

        return {
            "iterations": runs,
            "min_ms": round(min(durations_ms), 4),
            "avg_ms": round(sum(durations_ms) / runs, 4),
            "max_ms": round(max(durations_ms), 4),
            "total_ms": round(sum(durations_ms), 4),
        }

    @staticmethod
    def profile_cprofile_top_functions(
        func: Callable[..., Any],
        top_n: int = 10,
        *args: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Profile a callable with cProfile and return a formatted summary of top time consumers."""
        profiler = cProfile.Profile()
        profiler.enable()
        error: Optional[str] = None
        try:
            func(*args, **kwargs)
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
        func: Callable[..., Any],
        max_allowed_ms: float,
        *args: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Verify whether a callable executes within a specified millisecond budget."""
        prof = cls.profile_callable(func, *args, **kwargs)
        within_budget = prof["success"] and prof["execution_time_ms"] <= max_allowed_ms
        return {
            "within_budget": within_budget,
            "max_allowed_ms": max_allowed_ms,
            "actual_ms": prof["execution_time_ms"],
            "memory_peak_kb": prof["memory_peak_kb"],
            "success": prof["success"],
            "error": prof["error"],
        }