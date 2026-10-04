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

## Implementation notes (AP6, 2026-10-04)

- **The subset** is in `domain/output_schema.py`:
  - `type` (including type lists), `properties`, `required`, boolean `additionalProperties`;
  - `items`, `maxItems`;
  - `enum`, `format`, `description`.

  Anything else is refused when declared.
- **`ToolSpec.output_schema`** defaults to `None`, so hand-written tools are unchanged. When it is
  set, `ToolRegistry.execute` checks the JSON result. A mismatch is an audited failure with
  `error_kind="invalid_output"`, whose message lists paths, not values.
- **MCP** `outputSchema` is mapped when it fits the subset, and dropped with a log line when it
  does not. `structuredContent` is returned as JSON so the check applies.
