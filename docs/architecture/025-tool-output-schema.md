# ADR-025 — Tool output schemas and structured results

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

`ToolSpec` (`aura_core/domain/tools.py:112`) describes inputs but not outputs; tools return `str`.
Generated tools must bind to UI views, produce golden evaluation comparisons and truncate
row-aware — none of which work on opaque strings.

## Decision

1. Add `output_schema: Mapping[str, Any] | None = None` to `ToolSpec` — a JSON-Schema subset like
   the one `ParameterSpec` uses. `None` keeps today's behaviour for every existing tool.
2. `Tool.execute` keeps returning `str` for compatibility. Tools with an `output_schema` return JSON
   text, which `ToolRegistry.execute` validates after execution; a mismatch is an audited tool
   error, never passed through.
3. The MCP bridge maps an MCP tool's `outputSchema` into the same field.
4. `max_result_chars` truncation becomes row-aware for schema'd results (whole rows, plus a
   `truncated` flag).

## Alternatives rejected

- **A new tool interface.** It would split the registry and the policy path.
- **Parsing free text.** Unreliable, and untestable against goldens.

## Consequences

- UI manifests and evaluation suites can reference result fields by schema.
- No migration is needed for existing tools.
