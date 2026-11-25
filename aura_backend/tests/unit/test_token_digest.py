"""Deterministic digest of clipped tool output (ADR-047, row 28).

Under context pressure the token monitor shortens older tool output to its head and tail. A failing
test's name, a stack frame or a warning usually sits in the middle, so head-and-tail alone dropped
exactly the lines a later pass needs. ``digest_omitted`` keeps the salient lines of the omitted
part -- no model, bounded, idempotent. Pure functions here; the loop wiring is covered by
``test_loop_token_monitor.py`` (unchanged) plus one real-loop case below.
"""

from __future__ import annotations

from aura_core.domain.identifiers import PlanId, StepId
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.execution.token_monitor import compress_results, digest_omitted


def _output(middle: list[str]) -> str:
    head = "\n".join(f"collecting test_{i} ... ok" for i in range(60))
    tail = "\n".join(f"teardown step {i}" for i in range(40))
    filler = "\n".join(f"progress line {i} of the long run" for i in range(200))
    return "\n".join([head, filler, *middle, filler, tail])


def _plan_with(result: str) -> Plan:
    step = PlanStep(id=StepId("s1"), kind=StepKind.TOOL, tool="run_command", description="run the suite",
                    arguments={"command": "pytest"}).started().succeeded(result, 1.0)
    return Plan(id=PlanId.new(), goal=Goal(description="g"), steps=(step,))


class TestDigest:
    def test_failure_lines_in_the_middle_survive_the_clip(self) -> None:
        text = _output([
            "FAILED tests/test_billing.py::test_invoice_total - AssertionError: 12 != 13",
            "E   AssertionError: 12 != 13",
            "ValueError: unresolved reference to column 'billing'",
        ])
        _, results, saved = compress_results(_plan_with(text), {"s1": text})
        clipped = results["s1"]
        assert saved > 0 and len(clipped) < len(text)
        assert "omitted to save context" in clipped
        assert "FAILED tests/test_billing.py::test_invoice_total" in clipped
        assert "unresolved reference to column 'billing'" in clipped
        assert "progress line 7 of the long run" not in clipped, "plain noise is still dropped"
        assert clipped.startswith("collecting test_0") and clipped.endswith("teardown step 39")

    def test_repeats_are_counted_not_repeated(self) -> None:
        digest = digest_omitted("\n".join(["WARNING: deprecated call to foo()"] * 50 + ["ERROR: disk full"]))
        assert digest.count("deprecated call to foo()") == 1 and "(x50)" in digest and "ERROR: disk full" in digest

    def test_the_digest_is_bounded_in_lines_and_characters(self) -> None:
        digest = digest_omitted("\n".join(f"error number {i}: {'x' * 300}" for i in range(500)))
        assert len(digest) <= 500 + 40, "500 characters of lines plus the '(+N more)' tail"
        assert digest.count(" | ") <= 8 and "more)" in digest
        assert all(len(part) <= 120 for part in digest.split(" | "))

    def test_output_with_nothing_salient_gets_no_digest_and_the_old_shape(self) -> None:
        text = _output(["just a quiet progress note"])
        _, results, _ = compress_results(_plan_with(text), {"s1": text})
        assert "salient lines" not in results["s1"] and "omitted to save context" in results["s1"]

    def test_clipping_twice_saves_nothing_the_second_time(self) -> None:
        text = _output(["FAILED tests/test_a.py::test_x - boom"] + [f"error {i}: detail {i}" for i in range(30)])
        plan, results, _ = compress_results(_plan_with(text), {"s1": text})
        again_plan, again, saved = compress_results(plan, results)
        assert saved == 0 and again == results and again_plan is plan

    def test_total_size_stays_close_to_the_old_bound(self) -> None:
        text = _output([f"FAILED tests/test_{i}.py::test_case - boom {i}" for i in range(100)])
        _, results, _ = compress_results(_plan_with(text), {"s1": text})
        assert len(results["s1"]) < 2_000 + 100 + 500 + 120, "head + tail + marker + at most the digest"

    def test_a_very_long_line_is_cut_to_110_characters(self) -> None:
        # The digest copies lines the tool already returned and that were already guarded as tool
        # output; it adds nothing new, and cuts every line to 110 characters.
        digest = digest_omitted("error: " + "k" * 400)
        assert len(digest) <= 110
