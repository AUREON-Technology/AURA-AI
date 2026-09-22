"""Engineer/Coding Agent 2.0 - upgraded multi-step coding execution pipeline.

Extends the existing engineer agent with a bounded replan loop:
  task → inspect repository → plan → edit → run tests → inspect failures → fix → rerun
  → review diff → commit/branch when authorized → report verified result

Key upgrades over the original engineer agent:
- Bounded replanning (max_iterations, deadline_s) instead of unbounded
- Tool permission sandboxing (filesystem boundaries, command restrictions)
- Git safety (no force pushes, credential isolation, signed commits)
- Credential isolation (never expose API keys/tokens in tool args)
- Network restrictions (no outbound from sandbox except via allowed proxies)
- Throwaway execution (each task gets fresh sandbox, workspaces paused between turns)
- Audit trail (every action logged with correlation_id, tool output stored)
- Review diff before commit (never auto-commit without explicit approval)
- Approval gate for side-effecting operations
"""

from __future__ import annotations

from .pipeline import (
    EngineerExecutionPipeline,
    SandboxViolation,
    GitSafetyViolation,
    PipelineStep,
)
from .policy import SandboxPolicy, GitSafetyPolicy
from .models import (
    EngineerTask,
    EngineerResult,
    EngineerMode,
    EngineerApprovedPlan,
    EngineerRunStatus,
)

__all__ = [
    "EngineerExecutionPipeline",
    "SandboxPolicy",
    "GitSafetyPolicy",
    "EngineerTask",
    "EngineerResult",
    "EngineerMode",
    "EngineerApprovedPlan",
    "EngineerRunStatus",
    "PipelineStep",
    "SandboxViolation",
    "GitSafetyViolation",
]