"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/evaluation/harness.py` (`AgentEvaluator`), `evaluation/agent_runs.py`, and
`ToolRegistry.execute` with `tools/schema.py::validate_arguments`. This harness calls a tool
synchronously and never awaits it, so an `async` tool -- which is every real tool -- is reported
as passed without having run.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

import asyncio
import inspect
import json
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

_MAX_OUTPUT_PREVIEW_CHARS = 2048
_MAX_FUZZ_ITERATIONS = 1000


class _Outcome:
    """Result of one tool invocation: a value, an exception, or a timeout."""

    __slots__ = ("value", "exc", "timed_out")

    def __init__(self) -> None:
        self.value: Any = None
        self.exc: Optional[BaseException] = None
        self.timed_out = False


async def _await(awaitable: Any, timeout: Optional[float]) -> Any:
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout)


def _md_cell(value: Any) -> str:
    """Text that cannot break out of a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("`", "'")


class AgentToolTestHarness:
    """Harness for testing agent tool callables, input schemas, return types, fuzzing resilience, and latency.

    Superseded by ``evaluation/harness.py`` and not wired into the platform (ADR-036). It runs the
    callable it is given, in this process: it is a test helper, not a sandbox. Reads no files.

    Behaviour worth knowing:
    - a tool that returns a coroutine is run to completion on its own event loop (it is never
      reported as "passed" without having run);
    - ``timeout_seconds`` is real: the call runs on a worker thread and the case fails with stage
      ``timeout`` when it is exceeded. An ``async`` tool is cancelled; a *synchronous* tool cannot be
      interrupted, so its thread is left running (``still_running: True``) -- the result says so
      rather than pretending it stopped;
    - an empty suite is not a pass, and neither is a fuzz run that fuzzed nothing;
    - ``output_preview`` is bounded and JSON-safe.
    """

    @staticmethod
    def validate_payload_keys(
        payload: Dict[str, Any],
        required_keys: List[str],
    ) -> Dict[str, Any]:
        """Check that all required input keys exist in the test payload."""
        if not isinstance(payload, dict):
            return {"valid": False, "missing_keys": list(required_keys or []), "provided_keys": [],
                    "error": "payload must be a dict."}
        missing = [k for k in (required_keys or []) if k not in payload]
        return {
            "valid": len(missing) == 0,
            "missing_keys": missing,
            "provided_keys": sorted(str(k) for k in payload.keys()),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_preview(output: Any, max_chars: int = _MAX_OUTPUT_PREVIEW_CHARS) -> Any:
        """Return a safely-sized, JSON-safe preview of tool output."""
        if isinstance(output, str):
            if len(output) > max_chars:
                return output[:max_chars] + f"…[truncated {len(output) - max_chars} chars]"
            return output
        try:
            encoded = json.dumps(output)
        except (TypeError, ValueError):
            encoded = None
        if encoded is not None:
            if len(encoded) <= max_chars:
                return output
            return encoded[:max_chars] + f"…[truncated {len(encoded) - max_chars} chars]"
        text = repr(output)
        if len(text) > max_chars:
            return text[:max_chars] + f"…[truncated {len(text) - max_chars} chars]"
        return text

    @staticmethod
    def _run_coroutine(coro: Any, timeout: Optional[float] = None) -> Any:
        """Run a coroutine to completion from synchronous code on a fresh event loop.

        Inside a running loop the coroutine runs on a worker thread with its own loop (the old
        ``get_event_loop`` path blocked the running loop and left an unclosed one behind).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_await(coro, timeout))
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _await(coro, timeout)).result()

    @classmethod
    def _execute(cls, tool_callable: Callable[..., Any], data: Dict[str, Any],
                 timeout: Optional[float]) -> _Outcome:
        """Call the tool (sync or async) on a worker thread, bounded by ``timeout``."""
        outcome = _Outcome()

        def target() -> None:
            try:
                out = tool_callable(**data)
                if inspect.isawaitable(out):
                    out = asyncio.run(_await(out, timeout))
                outcome.value = out
            except asyncio.TimeoutError:
                outcome.timed_out = True
            except BaseException as exc:  # SystemExit / CancelledError from a tool must not escape
                outcome.exc = exc

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            outcome.timed_out = True
        return outcome

    @staticmethod
    def _clean_timeout(timeout_seconds: Any) -> Optional[float]:
        if timeout_seconds is None:
            return None
        try:
            seconds = float(timeout_seconds)
        except (TypeError, ValueError):
            return None
        if seconds != seconds or seconds <= 0:
            return None
        return seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def run_tool_case(
        cls,
        tool_name: str,
        tool_callable: Callable[..., Any],
        payload: Optional[Dict[str, Any]] = None,
        required_keys: Optional[List[str]] = None,
        expected_return_type: Optional[type] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a single tool callable with payload kwargs and verify keys, execution, and return type.

        Handles both synchronous and async (coroutine-returning) callables correctly.
        Async tools are run to completion via asyncio; they are NOT silently passed.
        """
        if not callable(tool_callable):
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "setup",
                "error": "Provided tool_callable is not callable.",
            }

        data = {} if payload is None else payload
        if not isinstance(data, dict) or not all(isinstance(k, str) for k in data):
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "input_validation",
                "error": "payload must be a dict with string keys.",
            }
        key_check = cls.validate_payload_keys(data, required_keys or [])
        if not key_check["valid"]:
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "input_validation",
                "key_check": key_check,
            }

        timeout = cls._clean_timeout(timeout_seconds)
        start = time.perf_counter()
        outcome = cls._execute(tool_callable, data, timeout)
        duration_ms = round((time.perf_counter() - start) * 1000.0, 4)

        if outcome.timed_out:
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "timeout",
                "duration_ms": duration_ms,
                "error": f"Execution exceeded timeout of {timeout}s.",
                "still_running": not inspect.iscoroutinefunction(tool_callable),
            }
        if outcome.exc is not None:
            exc = outcome.exc
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "execution",
                "duration_ms": duration_ms,
                "error": f"{type(exc).__name__}: {exc}",
            }

        raw_output = outcome.value
        type_ok = True
        if expected_return_type is not None:
            try:
                type_ok = isinstance(raw_output, expected_return_type)
            except TypeError:
                return {"tool_name": tool_name, "passed": False, "stage": "setup",
                        "error": "expected_return_type must be a type."}

        return {
            "tool_name": tool_name,
            "passed": type_ok,
            "stage": "assertion",
            "duration_ms": duration_ms,
            "return_type_valid": type_ok,
            "output_preview": cls._truncate_preview(raw_output),
        }

    @classmethod
    def run_suite(cls, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run a list of tool test case definitions and summarize pass/fail counts.

        ``all_passed`` is False for an empty suite: nothing ran, so nothing passed.
        """
        results: List[Dict[str, Any]] = []
        for case in cases if isinstance(cases, (list, tuple)) else []:
            if not isinstance(case, dict):
                results.append({"tool_name": "unnamed_tool", "passed": False, "stage": "setup",
                                "error": "Test case must be a dict."})
                continue
            fn = case.get("callable")
            if fn is None:
                results.append({
                    "tool_name": case.get("tool_name", "unnamed_tool"),
                    "passed": False,
                    "stage": "setup",
                    "error": "Missing 'callable' key in test case.",
                })
                continue

            res = cls.run_tool_case(
                tool_name=case.get("tool_name", "unnamed_tool"),
                tool_callable=fn,
                payload=case.get("payload"),
                required_keys=case.get("required_keys"),
                expected_return_type=case.get("expected_return_type"),
                timeout_seconds=case.get("timeout_seconds"),
            )
            results.append(res)

        passed_count = sum(1 for r in results if r.get("passed"))
        summary: Dict[str, Any] = {
            "total_cases": len(results),
            "passed_count": passed_count,
            "failed_count": len(results) - passed_count,
            "all_passed": bool(results) and passed_count == len(results),
            "results": results,
        }
        if not results:
            summary["error"] = "No test cases were run."
        return summary

    @staticmethod
    def validate_tool_schema(tool_schema_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Validate whether a tool dictionary adheres to OpenAI / Anthropic / Gemini tool schema specifications."""
        if not isinstance(tool_schema_dict, dict):
            return {"valid": False, "tool_name": "unknown", "issues_count": 1,
                    "issues": ["Tool definition must be a dictionary"]}
        issues: List[str] = []

        name = tool_schema_dict.get("name")
        if not name or not isinstance(name, str):
            issues.append("Tool definition missing string 'name'")

        desc = tool_schema_dict.get("description")
        if not desc or not isinstance(desc, str) or len(desc.strip()) < 10:
            issues.append("Tool description is missing or too brief (must explain intent)")

        parameters = tool_schema_dict.get("parameters")
        if parameters is not None:
            if not isinstance(parameters, dict):
                issues.append("Tool parameters must be a dictionary / JSON schema")
            elif parameters.get("type") != "object":
                issues.append("Tool parameters root type must be 'object'")

        return {
            "valid": len(issues) == 0,
            "tool_name": name if isinstance(name, str) and name else "unknown",
            "issues_count": len(issues),
            "issues": issues,
        }

    @classmethod
    def run_fuzz_tests(
        cls,
        tool_callable: Callable[..., Any],
        base_payload: Dict[str, Any],
        iterations: int = 10,
        *,
        timeout_seconds: float = 10.0,
    ) -> Dict[str, Any]:
        """Fuzz test a tool with unexpected edge cases (None, large strings, negative numbers) to ensure graceful error handling.

        One argument per trial is replaced by a hostile value. A trial that hangs past
        ``timeout_seconds`` counts as a crash. ``resilient`` is False when nothing could be fuzzed (an
        empty ``base_payload`` or ``iterations < 1``): that is "not tested", not "robust".
        """
        fuzz_values = [
            None,
            "",
            "   ",
            "A" * 5000,
            -999999,
            0,
            3.14159,
            [],
            {},
            True,
            False,
        ]

        if not callable(tool_callable):
            return {"resilient": False, "total_fuzz_trials": 0, "crashes_count": 0,
                    "handled_errors_count": 0, "crashes": [], "error": "tool_callable is not callable."}
        if not isinstance(base_payload, dict) or not base_payload:
            return {"resilient": False, "total_fuzz_trials": 0, "crashes_count": 0,
                    "handled_errors_count": 0, "crashes": [],
                    "error": "base_payload must be a non-empty dict; there is nothing to fuzz."}
        try:
            trials = max(0, min(int(iterations), _MAX_FUZZ_ITERATIONS))
        except (TypeError, ValueError):
            trials = 10
        timeout = cls._clean_timeout(timeout_seconds) or 10.0

        crashes: List[Dict[str, Any]] = []
        handled_errors: List[Dict[str, Any]] = []

        for i in range(trials):
            fuzzed_payload = dict(base_payload)
            target_key = random.choice(list(fuzzed_payload.keys()))
            fuzzed_payload[target_key] = random.choice(fuzz_values)

            outcome = cls._execute(tool_callable, {str(k): v for k, v in fuzzed_payload.items()}, timeout)
            if outcome.timed_out:
                crashes.append({
                    "iteration": i,
                    "payload": cls._truncate_preview(fuzzed_payload),
                    "unhandled_exception": f"Timed out after {timeout}s",
                })
            elif isinstance(outcome.exc, (ValueError, TypeError, KeyError)):
                handled_errors.append({"iteration": i, "payload": fuzzed_payload, "exception": str(outcome.exc)})
            elif outcome.exc is not None:
                crashes.append({
                    "iteration": i,
                    "payload": cls._truncate_preview(fuzzed_payload),
                    "unhandled_exception": f"{type(outcome.exc).__name__}: {outcome.exc}",
                })
            elif isinstance(outcome.value, dict) and outcome.value.get("error"):
                handled_errors.append({"iteration": i, "payload": fuzzed_payload, "result": outcome.value})

        result: Dict[str, Any] = {
            "resilient": trials > 0 and len(crashes) == 0,
            "total_fuzz_trials": trials,
            "crashes_count": len(crashes),
            "handled_errors_count": len(handled_errors),
            "crashes": crashes,
        }
        if trials == 0:
            result["error"] = "No fuzz trials were run."
        return result

    @classmethod
    def generate_tool_test_report(cls, suite_summary: Dict[str, Any]) -> str:
        """Render a Markdown test summary table for agent tools."""
        if not isinstance(suite_summary, dict):
            return "# Agent Tool Harness Test Report\n\n**Suite Result:** FAILED (no valid summary was given)"
        total = suite_summary.get("total_cases", 0)
        verdict = "PASSED" if suite_summary.get("all_passed") is True and total else (
            "NO TESTS RUN" if not total else "FAILED"
        )
        lines = [
            "# Agent Tool Harness Test Report",
            "",
            f"**Suite Result:** {verdict}",
            f"**Total Tested:** {total}",
            f"**Passed:** {suite_summary.get('passed_count', 0)}",
            f"**Failed:** {suite_summary.get('failed_count', 0)}",
            "",
            "| Tool Name | Stage | Passed | Duration (ms) | Notes |",
            "|---|---|---|---|---|",
        ]

        for r in suite_summary.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            passed_emoji = "✅" if r.get("passed") else "❌"
            lines.append(
                f"| `{_md_cell(r.get('tool_name'))}` | `{_md_cell(r.get('stage'))}` | {passed_emoji} | "
                f"{_md_cell(r.get('duration_ms', 'N/A'))} | {_md_cell(r.get('error', 'OK'))} |"
            )

        return "\n".join(lines)
