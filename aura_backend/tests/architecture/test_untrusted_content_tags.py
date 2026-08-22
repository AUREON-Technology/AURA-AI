"""Fitness: tools that fetch arbitrary external content are tagged so the
registry can taint the turn on their output (M4). See
tests/security/test_prompt_injection.py::TestUntrustedContentTaint for the
behavioural test of what the tag actually does.

There is no structural signal on a ToolSpec that says "this tool's output is
attacker-influenceable external content" other than the tag itself, so this
is deliberately a pinned allowlist, not a general heuristic: adding a new
tool that fetches arbitrary content (a Gmail/GitHub/Drive content-read tool,
another web-fetch variant) must add it here too, or this test won't catch
the gap on its own. What it does catch is the tag being silently dropped
from an existing entry.
"""

from __future__ import annotations

from aura_core.tools.builtin import default_tools

_EXPECTED_UNTRUSTED_CONTENT_TOOLS = {"fetch_url"}


def test_known_content_fetching_tools_are_tagged_untrusted_content() -> None:
    tools = default_tools(allowed_http_hosts=("example.com",))
    by_name = {t.spec.name: t.spec for t in tools}

    for name in _EXPECTED_UNTRUSTED_CONTENT_TOOLS:
        assert name in by_name, f"expected tool {name!r} to be registered"
        assert "untrusted_content" in by_name[name].tags, (
            f"{name!r} fetches arbitrary external content but lost its "
            "untrusted_content tag -- registry.execute() will no longer "
            "taint the turn on its output (M4)"
        )


def test_low_risk_tools_are_not_tagged() -> None:
    """Sanity check the tag isn't applied indiscriminately -- tools that
    can't return attacker-controlled text should stay untagged."""
    tools = default_tools()
    by_name = {t.spec.name: t.spec for t in tools}

    assert "calculator" in by_name
    assert "untrusted_content" not in by_name["calculator"].tags
