# Feature Discovery Planner — Claude Code Skill

A plan-first skill for brainstorming, researching, validating, and planning new features in an existing codebase, with specific guardrails for AURA AI. It does not implement the feature; it creates an evidence-backed plan and stops for review.

## Install in a project

Copy the `feature-discovery-planner` directory into the repository so the path is:

```text
<repository>/.claude/skills/feature-discovery-planner/SKILL.md
```

Or install for your user account by copying the directory to:

```text
~/.claude/skills/feature-discovery-planner/SKILL.md
```

## Invoke

```text
/feature-discovery-planner Add <feature idea> [repository path] [constraints/objective]
```

Examples:

```text
/feature-discovery-planner Add project goals and milestones to AURA Personal OS
/feature-discovery-planner Add image understanding; self-hosted inference only; no mobile models
/feature-discovery-planner Improve agent memory isolation; inspect the current repository and produce the safest incremental plan
```

The skill is manually invoked to avoid accidental activation. It may create a planning document in the repository's established planning-doc location (or `docs/feature-plans/<feature-slug>-implementation-plan.md` if appropriate), but it must not modify source code, dependencies, configuration, or infrastructure.
