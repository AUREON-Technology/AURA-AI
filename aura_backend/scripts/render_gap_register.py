"""Regenerate the generated half of docs/production/GAP_REGISTER.md from gap_register.yaml.

Everything above the ``## Totals`` heading is written prose and left untouched; ``## Totals`` and
``## All gaps`` are rebuilt. ``tests/architecture/test_gap_register_schema.py`` fails when the
committed Markdown differs from what this renders, so run it after every YAML edit:

    python aura_backend/scripts/render_gap_register.py          # from the repo root
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = ROOT / "docs" / "production" / "gap_register.yaml"
MD_PATH = ROOT / "docs" / "production" / "GAP_REGISTER.md"

SEVERITIES = ("P0", "P1", "P2", "P3")
STATUSES = ("open", "in_progress", "built_not_proven", "proven", "closed_stale")
_SUMMARY_CHARS = 160


def _one_line(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _summary(gap: dict) -> str:
    # The newest re-verification note supersedes the original current_state in the table, so a
    # row never shows a verdict the YAML itself has since corrected.
    notes = sorted(k for k in gap if k.startswith("reverified_"))
    if notes:
        latest = notes[-1]
        date = latest.removeprefix("reverified_").replace("_", "-")
        text = f"({date}) {gap[latest]}"
    else:
        text = gap["current_state"]
    text = _one_line(text)
    return text if len(text) <= _SUMMARY_CHARS else text[: _SUMMARY_CHARS - 1].rstrip() + "…"


def render_generated(gaps: list[dict]) -> str:
    lines = [
        "## Totals",
        "",
        "<!-- generated from gap_register.yaml by aura_backend/scripts/render_gap_register.py -->",
        "",
        "| Severity | " + " | ".join(STATUSES) + " |",
        "| --- | " + " | ".join("---" for _ in STATUSES) + " |",
    ]
    for sev in SEVERITIES:
        counts = [sum(1 for g in gaps if g["severity"] == sev and g["status"] == st) for st in STATUSES]
        lines.append(f"| {sev} | " + " | ".join(str(c) for c in counts) + " |")
    lines += [
        "",
        "## All gaps",
        "",
        "Sorted by status (`closed_stale` last), then severity. The full text, citations and "
        "`test`/`metric` fields are in the YAML; a row's summary is its newest re-verification "
        "note when one exists.",
        "",
        "| ID | Sev | Status | Component | Owner | Current state (summary) | Exit condition | Depends on |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    order = sorted(
        gaps,
        key=lambda g: (g["status"] == "closed_stale", SEVERITIES.index(g["severity"]), g["gap_id"]),
    )
    for g in order:
        deps = ", ".join(g.get("dependencies") or []) or "—"
        lines.append(
            f"| {g['gap_id']} | {g['severity']} | `{g['status']}` | {g['component']} | {g['owner']} "
            f"| {_summary(g)} | {_one_line(g['exit_condition'])} | {deps} |"
        )
    return "\n".join(lines) + "\n"


def render(md_text: str, gaps: list[dict]) -> str:
    head, sep, _ = md_text.partition("## Totals")
    if not sep:
        raise SystemExit(f"{MD_PATH} has no '## Totals' heading to regenerate from")
    return head + render_generated(gaps)


def main() -> int:
    gaps = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))["gaps"]
    MD_PATH.write_text(render(MD_PATH.read_text(encoding="utf-8"), gaps), encoding="utf-8")
    print(f"rendered {len(gaps)} gaps into {MD_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
