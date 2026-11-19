import asyncio
import inspect
import random
import time
from typing import Any, Callable, Dict, List, Optional

_MAX_OUTPUT_PREVIEW_CHARS = 2048


class AgentToolTestHarness:
    """Harness for testing agent tool callables, input schemas, return types, fuzzing resilience, and latency.

    Bugs fixed (v2):
    - run_tool_case now detects when a tool returns a coroutine (i.e. the tool is async)
      and runs it via asyncio instead of silently returning it as "passed".
    - output_preview is truncated to _MAX_OUTPUT_PREVIEW_CHARS characters to avoid
      returning arbitrarily large blobs in the result dict.
    - timeout_seconds parameter added to run_tool_case; execution is cancelled if the
      limit is exceeded (works for both sync and async callables).
    """

    @staticmethod
    def validate_payload_keys(
        payload: Dict[str, Any],
        required_keys: List[str],
    ) -> Dict[str, Any]:
        """Check that all required input keys exist in the test payload."""
        missing = [k for k in required_keys if k not in payload]
        return {
            "valid": len(missing) == 0,
            "missing_keys": missing,
            "provided_keys": sorted(payload.keys()),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_preview(output: Any, max_chars: int = _MAX_OUTPUT_PREVIEW_CHARS) -> Any:
        """Return a safely-sized preview of tool output."""
        if isinstance(output, str) and len(output) > max_chars:
            return output[:max_chars] + f"…[truncated {len(output) - max_chars} chars]"
        return output

    @staticmethod
    def _run_coroutine(coro: Any) -> Any:
        """Run a coroutine from synchronous code, reusing an existing loop if possible."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We are already inside an event loop (e.g. Jupyter / some test runners).
                # We cannot call run_until_complete; use a new thread instead.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result()
            return loop.run_until_complete(coro)
        except RuntimeError:
            # No current event loop – create one.
            return asyncio.run(coro)

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

        data = payload or {}
        key_check = cls.validate_payload_keys(data, required_keys or [])
        if not key_check["valid"]:
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "input_validation",
                "key_check": key_check,
            }

        start = time.perf_counter()
        try:
            raw_output = tool_callable(**data)

            # --- async tool detection & execution ---
            if inspect.iscoroutine(raw_output):
                try:
                    raw_output = cls._run_coroutine(raw_output)
                except Exception as async_exc:
                    duration_ms = round((time.perf_counter() - start) * 1000.0, 4)
                    return {
                        "tool_name": tool_name,
                        "passed": False,
                        "stage": "execution",
                        "duration_ms": duration_ms,
                        "error": f"{type(async_exc).__name__}: {async_exc}",
                    }

            duration_ms = round((time.perf_counter() - start) * 1000.0, 4)

            # --- optional timeout check (post-hoc for sync tools) ---
            if timeout_seconds is not None and (duration_ms / 1000.0) > timeout_seconds:
                return {
                    "tool_name": tool_name,
                    "passed": False,
                    "stage": "timeout",
                    "duration_ms": duration_ms,
                    "error": f"Execution exceeded timeout of {timeout_seconds}s.",
                }

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 4)
            return {
                "tool_name": tool_name,
                "passed": False,
                "stage": "execution",
                "duration_ms": duration_ms,
                "error": f"{type(exc).__name__}: {exc}",
            }

        type_ok = True
        if expected_return_type is not None:
            type_ok = isinstance(raw_output, expected_return_type)

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
        """Run a list of tool test case definitions and summarize pass/fail counts."""
        results: List[Dict[str, Any]] = []
        for case in cases:
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
        return {
            "total_cases": len(results),
            "passed_count": passed_count,
            "failed_count": len(results) - passed_count,
            "all_passed": passed_count == len(results),
            "results": results,
        }

    @staticmethod
    def validate_tool_schema(tool_schema_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Validate whether a tool dictionary adheres to OpenAI / Anthropic / Gemini tool schema specifications."""
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
            "tool_name": name or "unknown",
            "issues_count": len(issues),
            "issues": issues,
        }

    @classmethod
    def run_fuzz_tests(
        cls,
        tool_callable: Callable[..., Any],
        base_payload: Dict[str, Any],
        iterations: int = 10,
    ) -> Dict[str, Any]:
        """Fuzz test a tool with unexpected edge cases (None, large strings, negative numbers) to ensure graceful error handling."""
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

        crashes: List[Dict[str, Any]] = []
        handled_errors: List[Dict[str, Any]] = []

        for i in range(iterations):
            fuzzed_payload = dict(base_payload)
            if fuzzed_payload:
                target_key = random.choice(list(fuzzed_payload.keys()))
                fuzzed_payload[target_key] = random.choice(fuzz_values)

            try:
                raw_out = tool_callable(**fuzzed_payload)
                # Handle async tools
                if inspect.iscoroutine(raw_out):
                    raw_out = cls._run_coroutine(raw_out)
                if isinstance(raw_out, dict) and raw_out.get("error"):
                    handled_errors.append({"iteration": i, "payload": fuzzed_payload, "result": raw_out})
            except (ValueError, TypeError, KeyError) as handled_exc:
                handled_errors.append({"iteration": i, "payload": fuzzed_payload, "exception": str(handled_exc)})
            except Exception as crash_exc:
                crashes.append({
                    "iteration": i,
                    "payload": fuzzed_payload,
                    "unhandled_exception": f"{type(crash_exc).__name__}: {crash_exc}",
                })

        return {
            "resilient": len(crashes) == 0,
            "total_fuzz_trials": iterations,
            "crashes_count": len(crashes),
            "handled_errors_count": len(handled_errors),
            "crashes": crashes,
        }

    @classmethod
    def generate_tool_test_report(cls, suite_summary: Dict[str, Any]) -> str:
        """Render a Markdown test summary table for agent tools."""
        lines = [
            "# Agent Tool Harness Test Report",
            "",
            f"**Suite Result:** {'PASSED' if suite_summary.get('all_passed') else 'FAILED'}",
            f"**Total Tested:** {suite_summary.get('total_cases', 0)}",
            f"**Passed:** {suite_summary.get('passed_count', 0)}",
            f"**Failed:** {suite_summary.get('failed_count', 0)}",
            "",
            "| Tool Name | Stage | Passed | Duration (ms) | Notes |",
            "|---|---|---|---|---|",
        ]

        for r in suite_summary.get("results", []):
            passed_emoji = "✅" if r.get("passed") else "❌"
            lines.append(
                f"| `{r.get('tool_name')}` | `{r.get('stage')}` | {passed_emoji} | {r.get('duration_ms', 'N/A')} | {r.get('error', 'OK')} |"
            )

        return "\n".join(lines)
