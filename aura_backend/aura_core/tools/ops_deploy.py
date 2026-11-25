"""Deploy, redeploy and roll back by starting the operator's own CI workflows (ADR-049).

The agent never holds a Railway token or an SSH key and never runs a deploy command. It asks GitHub to
start a workflow that already exists, with the same ``workflow_dispatch`` a person would press, so a
deployment keeps every gate that workflow has (CI, secrets, environments) and shows up in the same
run history. What this module adds is the fence around that request:

* **An allowlist, from the operator.** ``AURA_OPS_DEPLOY_WORKFLOWS`` names workflow *files*
  (``deploy-backend.yml`` or ``deploy-backend.yml:sha`` -- the part after ``:`` is the workflow input
  that takes a commit). Nothing else can be dispatched, and with no allowlist the tools do not exist.
* **A fixed repository** (the deployment's default repository) and **fixed refs**
  (``AURA_OPS_DEPLOY_REFS``, default ``main``): a deploy cannot be pointed at a branch of the model's
  choosing.
* **A human in the loop on every dispatch.** ``side_effecting`` at ``CONFIDENTIAL`` sensitivity, which
  ``ToolSpec`` forces into ``requires_confirmation``, plus the ``engineer_agent`` capability and a
  written ``reason`` that goes into the audit record.
* **Rollback is a redeploy of an older commit**, and only for a workflow that declared an input for
  one. A workflow that cannot target a commit cannot be "rolled back" by this tool; the answer is then
  a revert pull request, and the error says so.

A restart is a redeploy of the same ref: there is no tool that runs ``restart`` anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..domain.errors import ToolExecutionError
from ..domain.tools import NETWORK_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool

__all__ = ["DeployWorkflow", "parse_deploy_workflows", "build_deploy_tools", "DEPLOY_TOOL_NAMES"]

DEPLOY_TOOL_NAMES: frozenset[str] = frozenset({"list_workflow_runs", "deploy_release", "rollback_release"})
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.ya?ml$")
_INPUT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,40}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


@dataclass(frozen=True)
class DeployWorkflow:
    file: str
    #: The workflow input that takes a commit SHA; ``""`` when the workflow cannot target one.
    sha_input: str = ""


def parse_deploy_workflows(entries: Sequence[str]) -> tuple[DeployWorkflow, ...]:
    """``("a.yml", "b.yml:sha")`` -> workflows. A malformed entry is a configuration error, not ignored."""
    out: list[DeployWorkflow] = []
    for entry in entries:
        file, _, sha_input = entry.strip().partition(":")
        if not _FILE.match(file) or (sha_input and not _INPUT.match(sha_input)):
            raise ValueError(f"{entry!r} is not 'workflow.yml' or 'workflow.yml:input_name'")
        out.append(DeployWorkflow(file, sha_input))
    return tuple(out)


def build_deploy_tools(
    client_resolver: Any,
    *,
    repo: str,
    workflows: Sequence[DeployWorkflow],
    refs: Sequence[str] = ("main",),
) -> list[Tool]:
    """The deploy tools, or nothing unless a repository and at least one workflow are configured."""
    if not repo or not workflows or not refs:
        return []
    allowed = {w.file: w for w in workflows}
    ref_list = [r.strip() for r in refs if r.strip()]
    names = ", ".join(sorted(allowed))

    async def client(context: ToolContext) -> Any:
        found = await client_resolver(context.user_id)
        if found is None:
            raise ToolExecutionError("GitHub is not connected for this user: connect it (or have an operator "
                                     "configure AURA_GITHUB_TOKEN) before using deploy tools", tool="deploy")
        return found

    def pick(name: str, workflow: str) -> DeployWorkflow:
        if workflow not in allowed:
            raise ToolExecutionError(f"{workflow!r} is not a deployable workflow on this deployment",
                                     tool=name, details={"allowed": sorted(allowed)})
        return allowed[workflow]

    def check_reason(name: str, reason: str) -> str:
        text = " ".join(reason.split())
        if len(text) < 10:
            raise ToolExecutionError("reason must say why, in at least a sentence: it goes into the audit record",
                                     tool=name)
        return text[:300]

    @tool(
        "list_workflow_runs",
        "Recent runs of a deploy workflow: status, conclusion, commit, link. Use it to see whether a deploy "
        "started, finished or failed. Workflows: " + names,
        param("workflow", "string", "Workflow file, e.g. deploy-backend.yml.", max_length=100),
        param("limit", "integer", "How many runs (default 5, max 20).", required=False, default=5, minimum=1, maximum=20),
        category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.PERSONAL, timeout_s=20.0,
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def list_workflow_runs(workflow: str, context: ToolContext, limit: int = 5) -> str:
        pick("list_workflow_runs", workflow)
        runs = await (await client(context)).list_workflow_runs(repo, workflow, limit=limit)
        return json.dumps({"workflow": workflow, "repository": repo, "runs": runs, "read_only": True}, default=str)

    @tool(
        "deploy_release",
        "Start a deploy by dispatching the operator's own CI workflow (the same button a person presses): "
        "it keeps every gate that workflow has. Asks the user to confirm first. Also the way to restart "
        "(redeploy the same ref). Afterwards use list_workflow_runs, then check_platform_health. "
        "Workflows: " + names + ". Refs: " + ", ".join(ref_list),
        param("workflow", "string", "Workflow file to dispatch.", max_length=100),
        param("reason", "string", "Why this deploy is being started (audited).", max_length=300),
        param("ref", "string", "Branch to deploy (default: the first allowed).", required=False, max_length=100),
        category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
        required_capability="engineer_agent", timeout_s=30.0, tags=(NETWORK_TAG,),
    )
    async def deploy_release(workflow: str, reason: str, context: ToolContext, ref: str | None = None) -> str:
        name = "deploy_release"
        pick(name, workflow)
        why = check_reason(name, reason)
        target = (ref or ref_list[0]).strip()
        if target not in ref_list:
            raise ToolExecutionError(f"{target!r} is not a deployable ref", tool=name, details={"allowed": ref_list})
        message = await (await client(context)).dispatch_workflow(repo, workflow, ref=target)
        return json.dumps({"dispatched": True, "workflow": workflow, "ref": target, "reason": why,
                           "result": message, "next": "list_workflow_runs, then check_platform_health"})

    @tool(
        "rollback_release",
        "Roll back by redeploying an earlier commit through the same CI workflow. Only for a workflow that "
        "takes a commit input; asks the user to confirm first. Afterwards check_platform_health with "
        "expected_git_sha set to that commit. Workflows: " + names,
        param("workflow", "string", "Workflow file to dispatch.", max_length=100),
        param("sha", "string", "The earlier commit to redeploy (7-40 hex characters).", max_length=40),
        param("reason", "string", "Why this rollback is needed (audited).", max_length=300),
        category=ToolCategory.DEVELOPMENT, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
        required_capability="engineer_agent", timeout_s=30.0, tags=(NETWORK_TAG,),
    )
    async def rollback_release(workflow: str, sha: str, reason: str, context: ToolContext) -> str:
        name = "rollback_release"
        chosen = pick(name, workflow)
        why = check_reason(name, reason)
        if not chosen.sha_input:
            raise ToolExecutionError(
                f"{workflow} cannot deploy a chosen commit, so it cannot be rolled back by this tool; revert "
                "the change on the branch and let the normal deploy run", tool=name)
        if not _SHA.match(sha.strip()):
            raise ToolExecutionError("sha must be 7-40 hexadecimal characters", tool=name)
        host = await client(context)
        if not await host.commit_is_ancestor(repo, sha.strip(), ref_list[0]):
            raise ToolExecutionError(
                f"{sha.strip()[:12]} is not part of {ref_list[0]}'s history (it may exist only on a fork or an "
                "unmerged branch), so it will not be deployed", tool=name)
        message = await host.dispatch_workflow(
            repo, workflow, ref=ref_list[0], inputs={chosen.sha_input: sha.strip().lower()})
        return json.dumps({"dispatched": True, "workflow": workflow, "rollback_to": sha.strip().lower(),
                           "reason": why, "result": message,
                           "next": "list_workflow_runs, then check_platform_health with expected_git_sha"})

    return [list_workflow_runs, deploy_release, rollback_release]
