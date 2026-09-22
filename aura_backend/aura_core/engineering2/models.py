"""Engineer task and result models for the 2.0 pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class EngineerMode(Enum):
    """Execution mode for the engineer agent."""

    SELF_CONTAINED = "self_contained"  # sandbox only, no git, no external
    GIT_WORKFLOW = "git_workflow"  # sandbox + git operations
    FULL = "full"  # sandbox + git + external tool access (requires explicit approval)


class EngineerRunStatus:
    """Plain string constants, for compatibility with domain layer."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EngineerTaskStatus(Enum):
    """Status of an engineer task."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REPLANNING = "replanning"
    VERIFIED = "verified"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class EngineerTask:
    """A coding task for the engineer agent 2.0.

    Encapsulates the full lifecycle from task creation through verified result.
    """

    id: str
    query: str  # Natural language description of what to build/modify
    owner_id: str  # User who submitted the task
    mode: EngineerMode = EngineerMode.SELF_CONTAINED
    max_iterations: int = 8  # bounded replan loop
    deadline_s: float = 300.0  # max seconds per task
    workspace_id: str | None = None  # assigned workspace, auto-created if None
    granted_capabilities: frozenset[str] = field(default_factory=frozenset)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Internal state (populated by the pipeline)
    current_iteration: int = 0
    plan: list[str] | None = None  # sequenced plan steps
    last_error: str | None = None
    final_result: str | None = None  # description of what was accomplished
    verified: bool = False  # whether the result passed final verification
    audit_log: list[dict[str, Any]] = field(default_factory=list)  # immutable action log


@dataclass
class EngineerResult:
    """Result of engineer agent execution."""

    task_id: str
    success: bool
    completed_at: datetime
    output: str  # Human-readable summary of what was accomplished
    changes: list[dict[str, Any]] = field(default_factory=list)  # File changes made
    test_results: dict[str, Any] = field(default_factory=dict)  # Test execution results
    errors: list[str] = field(default_factory=list)  # Collected errors
    audit_url: str | None = None  # Link to full audit trail
    duration_s: float = 0.0  # Total execution time
    verified: bool = False  # Whether the result passed final verification


@dataclass
class EngineerApprovedPlan:
    """A plan that has been approved for execution.

    Side-effecting operations (file writes, git commits, command execution)
    must pass through this approval gate.
    """

    task_id: str
    plan_steps: list[str]
    approved_by: str  # "system" or user/actor identifier
    approved_at: float
    conditions: dict[str, Any] = field(default_factory=dict)
    auto_approved: bool = False  # whether system auto-approved or human approval required