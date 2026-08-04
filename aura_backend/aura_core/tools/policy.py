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
    ) -> ToolPolicyDecision:
        audit = {
            "tool": spec.name,
            "sensitivity": spec.sensitivity.name,
            "side_effecting": spec.side_effecting,
            "tainted": context.tainted,
        }

        if spec.name in self._denylist:
            return ToolPolicyDecision.deny("denied_by_configuration", **audit)

        if self._allowlist and spec.name not in self._allowlist:
            return ToolPolicyDecision.deny("not_in_allowlist", **audit)

        if spec.sensitivity > self._max_sensitivity:
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
