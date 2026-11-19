import time
from typing import Any, Callable, Dict, List, Optional


class AgentToolTestHarness:
    """Harness for testing agent tool callables, input schemas, return types, and latency."""

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

    @classmethod
    def run_tool_case(
        cls,
        tool_name: str,
        tool_callable: Callable[..., Any],
        payload: Optional[Dict[str, Any]] = None,
        required_keys: Optional[List[str]] = None,
        expected_return_type: Optional[type] = None,
    ) -> Dict[str, Any]:
        """Execute a single tool callable with payload kwargs and verify keys, execution, and return type."""
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
            output = tool_callable(**data)
            duration_ms = round((time.perf_counter() - start) * 1000.0, 4)
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
            type_ok = isinstance(output, expected_return_type)

        return {
            "tool_name": tool_name,
            "passed": type_ok,
            "stage": "assertion",
            "duration_ms": duration_ms,
            "return_type_valid": type_ok,
            "output_preview": output,
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