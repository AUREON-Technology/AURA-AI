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

Two layers sit around those rules and can only make a call *stricter* (ADR-041):

* **Declarative rules** (``AURA_TOOL_POLICY_FILE``, :mod:`.policy_rules`) -- operator-written
  deny / require-confirmation rules over a tool's name *and arguments*, scoped by persona and
  sensitivity. They run before everything above; a rule that fires is named in the decision's
  reason (``policy_rule_deny:<id>``) so the audit log says which one.
* **Autonomy profile** (``context.autonomy``, set per user) -- ``strict`` confirms every side
  effect and denies tools tagged ``network``; ``standard`` is the rules above unchanged;
  ``autonomous`` is honoured only when the deployment enabled it, and it never skips a taint,
  remote-origin, tool-declared or non-workspace-local confirmation. Every workspace-local tool the
  policy allows is already prompt-free on the server side, and a paired desktop's own approval
  cannot be waived from here, so on today's tool set ``autonomous`` waives nothing: it is the
  profile a future workspace-local tool could opt into, and the switch that proves nothing looser
  than ``standard`` is reachable.
"""

from __future__ import annotations

from typing import Sequence

from typing import Any, Mapping

from ..domain.tools import NETWORK_TAG, ToolPolicyDecision, ToolSensitivity, ToolSpec
from ..ports.runtime import ToolContext
from .policy_rules import PolicyRuleSet, load_policy_rules

__all__ = ["ToolPolicy", "AUTONOMY_PROFILES", "DEFAULT_AUTONOMY"]

#: The per-user review profiles, loosest last. ``standard`` is today's behaviour.
AUTONOMY_PROFILES = ("strict", "standard", "autonomous")
DEFAULT_AUTONOMY = "standard"


class ToolPolicy:
    def __init__(
        self,
        *,
        allowlist: Sequence[str] = (),
        denylist: Sequence[str] = (),
        require_confirmation_for_side_effects: bool = True,
        max_sensitivity: ToolSensitivity = ToolSensitivity.SYSTEM,
        block_side_effects_when_tainted: bool = True,
        rules: PolicyRuleSet | None = None,
        allow_autonomous: bool = False,
    ) -> None:
        self._allowlist = frozenset(allowlist)
        self._denylist = frozenset(denylist)
        self._confirm_side_effects = require_confirmation_for_side_effects
        self._max_sensitivity = max_sensitivity
        self._block_when_tainted = block_side_effects_when_tainted
        self._rules = rules if rules is not None else PolicyRuleSet()
        # Whether a user's ``autonomous`` profile is honoured. Off by default: it is the one
        # profile that can ask for less than ``standard`` does, so an operator opts in to it.
        self._allow_autonomous = allow_autonomous

    @property
    def rules(self) -> PolicyRuleSet:
        return self._rules

    @property
    def allow_autonomous(self) -> bool:
        return self._allow_autonomous

    def effective_autonomy(self, context: ToolContext) -> str:
        """The profile that governs this call: the user's, unless it is one this deployment does
        not honour (``autonomous`` while disabled, or an unknown value) -- both fall back to
        ``standard``, never to something looser."""
        profile = getattr(context, "autonomy", None) or DEFAULT_AUTONOMY
        if profile == "autonomous" and not self._allow_autonomous:
            return DEFAULT_AUTONOMY
        return profile if profile in AUTONOMY_PROFILES else DEFAULT_AUTONOMY

    def evaluate(
        self,
        spec: ToolSpec,
        *,
        context: ToolContext,
        capabilities: Sequence[str] = (),
        confirmed: bool = False,
        host_routed: bool = False,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolPolicyDecision:
        """``arguments`` are the call's validated arguments, for the declarative rules; a caller
        that omits them gets confirmation from any rule that needs one (never a silent pass).

        ``host_routed`` says this call will run on the user's own machine through a paired
        ``aura_hostd`` rather than in the server-side sandbox -- the routing
        ``CompositeSandboxRunner`` performs. The registry works it out; it changes what
        ``workspace_local`` can promise (see below)."""
        profile = self.effective_autonomy(context)
        verdict = None
        if self._rules:
            verdict = self._rules.evaluate(
                spec.name, arguments, persona=context.agent_id,
                sensitivity=spec.sensitivity.name, host_routed=host_routed,
            )
        if verdict is not None and verdict.action == "deny":
            return ToolPolicyDecision.deny(
                verdict.reason, tool=spec.name, sensitivity=spec.sensitivity.name,
                side_effecting=spec.side_effecting, tainted=context.tainted,
                host_routed=host_routed, autonomy=profile, rule=verdict.rule_id,
                rule_detail=verdict.detail,
            )
        if profile == "strict" and NETWORK_TAG in spec.tags:
            return ToolPolicyDecision.deny(
                "autonomy_strict_denies_network", tool=spec.name,
                sensitivity=spec.sensitivity.name, side_effecting=spec.side_effecting,
                tainted=context.tainted, host_routed=host_routed, autonomy=profile,
            )

        decision = self._evaluate_base(
            spec, context=context, capabilities=capabilities, confirmed=confirmed,
            host_routed=host_routed,
        )
        if not decision.allowed:
            return decision

        stricter: tuple[str, dict[str, Any]] | None = None
        if verdict is not None and not confirmed:  # a "confirm" verdict (deny returned above)
            stricter = (verdict.reason, {"rule": verdict.rule_id, "rule_detail": verdict.detail})
        elif profile == "strict" and spec.side_effecting and not confirmed:
            stricter = ("autonomy_strict_side_effect", {})
        if stricter is not None and not decision.requires_confirmation:
            return ToolPolicyDecision.confirm(
                stricter[0], **{**dict(decision.audit_fields), "autonomy": profile, **stricter[1]}
            )
        return decision

    def _evaluate_base(
        self,
        spec: ToolSpec,
        *,
        context: ToolContext,
        capabilities: Sequence[str],
        confirmed: bool,
        host_routed: bool,
    ) -> ToolPolicyDecision:
        """The four rules in the module docstring -- unchanged by rules or profiles."""
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
        # Asked through `permits_tool` so a prefix grant (the engineer's `mcp_`, audit T-03) is
        # honoured here exactly as at declaration time.
        if not context.permits_tool(spec.name):
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
            if host_routed and (context.metadata or {}).get("origin") == "remote":
                # WP8 (D-10b/D-10c): a turn from a phone or the web may change the person's own
                # computer only one approved call at a time, approved on that device. Anyone
                # holding a stolen session must still get each change past the person.
                return ToolPolicyDecision.confirm(
                    "side_effect_on_user_machine_from_remote_device", **audit
                )
            if spec.requires_confirmation:
                # `ToolSpec` only sets this itself for a tool that is *not* workspace-local, so a
                # workspace-local spec carrying it asked for it explicitly. The exemption exists
                # to spare prompts nobody should be asked, not to override one a tool declared.
                return ToolPolicyDecision.confirm("tool_requires_confirmation", **audit)
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
        """Build from :class:`~aura_core.config.settings.ToolSettings`.

        Loads and validates ``policy_file`` (``AURA_TOOL_POLICY_FILE``) here, so a bad rule stops
        the process at start-up with the rule's index in the message rather than at first use.
        """
        return cls(
            allowlist=settings.allowlist,
            denylist=settings.denylist,
            require_confirmation_for_side_effects=settings.require_confirmation_for_side_effects,
            rules=load_policy_rules(getattr(settings, "policy_file", "") or ""),
            allow_autonomous=bool(getattr(settings, "allow_autonomous_profile", False)),
        )
