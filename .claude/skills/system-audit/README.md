# System Audit — Claude Code Skill

A standalone, report-first audit skill for reviewing an entire codebase or selected module. It combines full-codebase review discipline with separate UI/UX, workflow, logic, architecture, security, performance, reliability, and test/evaluation lenses.

## Install

Copy the `system-audit` folder into either:

- **Personal skill:** `~/.claude/skills/system-audit/` (available across projects)
- **Project skill:** `<project>/.claude/skills/system-audit/` (shared with that project when committed)

The final structure should be:

```text
.claude/skills/system-audit/
├── SKILL.md
└── references/
    ├── audit-checklist.md
    └── report-template.md
```

Restart or start a new Claude Code session if the skill does not appear, then invoke `/system-audit`.

## Usage examples

```text
/system-audit "E:\Projects\AURA" objective="Find UI/UX, logic, and workflow defects and make a prioritized upgrade roadmap" mode=full

/system-audit ./aura_client module="chat and memory UI" objective="Find broken states, navigation errors, sync issues, and UX improvements" mode=module

/system-audit ./backend ./client module=authentication mode=module baseline=docs/audits/latest.md

/system-audit . mode=delta
```

You can add paths to previous audits, ADRs, product specifications, screenshots, design references, or acceptance criteria. The skill checks them against the implementation rather than assuming they are current.

## What it produces

When permitted, it creates `.claude/audits/<run-id>/` containing:

- `audit-report.md` — executive summary, system map, coverage, evidence-backed findings, verification record, and residual risks.
- `remediation-roadmap.md` — dependency-aware phases, priorities, relative effort, implementation scope, and acceptance criteria.
- `audit-manifest.md` — baseline, scope, coverage matrix, and exact checks run/not run.

The skill must not change product code, install dependencies, migrate databases, or deploy. It can only write its audit artifacts. Test and runtime verification is reported only when actually performed; missing evidence remains explicitly unverified.

## Design choices

- One `/system-audit` entry point with optional `module` and `objective` values.
- The entire system is audited by default; module mode still traces related call paths and contracts.
- Existing specifications, ADRs, and project rules are preserved unless there is evidence of a real conflict or blocker.
- Findings distinguish confirmed defects from recommendations and uncertain hypotheses.
- Algorithm/architecture improvements include trade-offs and measurable validation criteria.
- Long audit runs track coverage instead of claiming that unread modules have been reviewed.

## References used during design

- Claude Code skills documentation: https://code.claude.com/docs/en/skills
- `raaaf/claude-skills` full-audit/design-audit workflows: https://github.com/raaaf/claude-skills
- `amragl/codebase-review`: https://github.com/amragl/codebase-review
- `wonjyou/design-audit`: https://github.com/wonjyou/design-audit
- `borghei/Claude-Skills` design-auditor: https://github.com/borghei/Claude-Skills
