"""Tool authorisation policy.

Separated from the registry because *whether* a tool may run is a different concern from *how*
it runs, and because a deployment will want to change this without touching dispatch.

Four rules, in order of strictness:

1. **Denylist / allowlist.** Configuration wins over everything.
2. **Capability grant.** A tool declaring ``required_capability`` needs an explicit grant on the
   caller. Absence of a grant is a denial, not a default-allow.
3. **Taint.** When the turn's context contained quarantined content (ADR-006), a side-effecting
   tool cannot run without user confirmation. This is the rule that closes the SEC-02 attack
   chain: a poisoned document can no longer cause a Drive ingestion, because the ingestion tool
   sees a tainted context and stops for confirmation.

   Tools marked ``workspace_local`` are exempt, because their "side effect" is a file in a
   scratch directory that is destroyed with the workspace -- it reaches no user data and nothing
   that outlives the task. The exemption exists to keep the gate meaningful rather than to
   weaken it: without it, one cloned repository taints a turn and every subsequent command in a
   multi-step engineering task asks for confirmation, and a prompt asked dozens of times per
   task is one people approve without reading. Anything that leaves the workspace (a push, any
   forge write) is not workspace-local and keeps every rule here. See ADR-014.
4. **Sensitivity.** Confidential and system-level tools always require confirmation for
   side-effecting operations, tainted or not.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.tools import ToolPolicyDecision, ToolSensitivity, ToolSpec
from ..ports.runtime import ToolContext

__all__ = ["ToolPolicy"]


class ToolPolicy:
    def __init__(
        self,
        *,
        allowlist: Sequence[str] = (),
        denylist: Sequence[str] = (),
        require_confirmation_for_side_effects: bool = True,
        max_sensitivity: ToolSensitivity = ToolSensitivity.SYSTEM,
        block_side_effects_when_tainted: bool = True,
    ) -> None:
        self._allowlist = frozenset(allowlist)
        self._denylist = frozenset(denylist)
        self._confirm_side_effects = require_confirmation_for_side_effects
        self._max_sensitivity = max_sensitivity
        self._block_when_tainted = block_side_effects_when_tainted

    def evaluate(
        self,
        spec: ToolSpec,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed: bool = False,
        host_routed: bool = False,
    ) -> ToolPolicyDecision:
        """``host_routed`` says this call will run on the user's own machine through a paired
        ``aura_hostd`` rather than in the server-side sandbox -- the routing
        ``CompositeSandboxRunner`` performs. The registry works it out; it changes what
        ``workspace_local`` can promise (see below)."""
        audit = {
            "tool": spec.name,
            "sensitivity": spec.sensitivity.name,
            "side_effecting": spec.side_effecting,
            "tainted": context.tainted,
            "host_routed": host_routed,
        }

        if spec.name in self._denylist:
            return ToolPolicyDecision.deny("denied_by_configuration", **audit)

        if self._allowlist and spec.name not in self._allowlist:
            return ToolPolicyDecision.deny("not_in_allowlist", **audit)

        # Agent-level grant (Phase C1). ``allowed_tools=None`` means the caller set no
        # restriction beyond its own capabilities -- the orchestrator persona and every caller
        # that predates the agent registry. A non-None set is an allowlist scoped to one turn's
        # resolved agent, enforced here rather than only at declaration time so a call that
        # somehow reaches dispatch outside what was ever offered (a hallucinated tool name, a
        # future non-conforming caller) still fails closed and is audited like any other denial.
        if context.allowed_tools is not None and spec.name not in context.allowed_tools:
            return ToolPolicyDecision.deny("outside_agent_grant", **audit)

        effective_max_sensitivity = (
            self._max_sensitivity if context.max_sensitivity is None
            else min(self._max_sensitivity, context.max_sensitivity)
        )
        if spec.sensitivity > effective_max_sensitivity:
            return ToolPolicyDecision.deny("sensitivity_exceeds_policy", **audit)

        if spec.required_capability and spec.required_capability not in set(capabilities):
            # Fails closed. A tool guarded by a capability is guarded because running it without
            # one is unsafe, so "no grant" must mean "no", never "probably fine".
            return ToolPolicyDecision.deny(
                f"missing_capability:{spec.required_capability}", **audit
            )

        if confirmed:
            # The user has already approved this specific call; the confirmation rules below
            # exist to obtain exactly this.
            return ToolPolicyDecision.allow("user_confirmed", **audit)

        # Work confined to an ephemeral agent workspace is allowed to proceed, tainted or not.
        # The confinement is the justification: the "side effect" is a file in a scratch
        # directory that is destroyed when the workspace expires, reaching no user data and
        # nothing outside itself. Crossing out of that workspace -- git_push, any GitHub write,
        # any personal-OS or knowledge write -- is not workspace-local and keeps every rule
        # below, which is where the confirmation gate does its actual work.
        #
        # Without this, one clone taints the turn and every subsequent write_file, edit_file,
        # run_command and git_commit in a multi-turn engineering session demands confirmation.
        # A gate asked dozens of times per task is one people learn to click through, so the
        # exemption protects the prompts that matter by removing the ones that do not. ADR-014
        # records the reasoning; tests/architecture/test_workspace_local_tools.py pins the set.
        #
        # The confinement only exists in the sandbox. Routed to a paired desktop, the same
        # run_command or write_file acts on the user's real files as their OS account: nothing is
        # destroyed on expiry, and the daemon's own allowlist is the only other check. So on the
        # user's machine a tainted turn is held for confirmation like any other side effect --
        # injected content must not be able to run commands there unasked -- whatever
        # block_side_effects_when_tainted says, since that setting was never meant to cover
        # someone's own computer. An untainted turn keeps the exemption: the user asked for the
        # work, and the daemon still gates untrusted folders itself.
        if spec.workspace_local and spec.side_effecting:
            if host_routed and context.tainted:
                return ToolPolicyDecision.confirm(
                    "side_effect_on_user_machine_in_tainted_context", **audit
                )
            return ToolPolicyDecision.allow("workspace_local_side_effect", **audit)

        if context.tainted and spec.side_effecting and self._block_when_tainted:
            return ToolPolicyDecision.confirm(
                "side_effect_in_tainted_context", **audit
            )

        if spec.requires_confirmation:
            return ToolPolicyDecision.confirm("tool_requires_confirmation", **audit)

        if (
            self._confirm_side_effects
            and spec.side_effecting
            and spec.sensitivity >= ToolSensitivity.CONFIDENTIAL
        ):
            return ToolPolicyDecision.confirm("confidential_side_effect", **audit)

        return ToolPolicyDecision.allow("policy_ok", **audit)

    @classmethod
    def from_settings(cls, settings) -> "ToolPolicy":
        """Build from :class:`~aura_core.config.settings.ToolSettings`."""
        return cls(
            allowlist=settings.allowlist,
            denylist=settings.denylist,
            require_confirmation_for_side_effects=settings.require_confirmation_for_side_effects,
        )
