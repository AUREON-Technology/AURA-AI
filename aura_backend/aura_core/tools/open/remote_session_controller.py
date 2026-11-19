from typing import Any, Dict, Optional


class RemoteSessionController:
    """State controller for pausing, resuming, cancelling, and redirecting agent sessions safely."""

    VALID_ACTIONS = {"pause", "resume", "cancel", "redirect"}

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve session state or initialize a default running session."""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "session_id": session_id,
                "state": "running",
                "target": None,
                "checkpoint_saved": True,
                "last_reason": None,
            }
        return self._sessions[session_id]

    def apply_action(
        self,
        session_id: str,
        action: str,
        reason: Optional[str] = None,
        redirect_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply a session control action and record checkpoint and reason metadata."""
        norm_action = action.strip().lower()
        if norm_action not in self.VALID_ACTIONS:
            return {
                "updated": False,
                "session_id": session_id,
                "error": f"Invalid action '{action}'. Allowed: {sorted(self.VALID_ACTIONS)}",
            }

        session = self.get_or_create_session(session_id)
        prev_state = session["state"]

        if norm_action == "pause":
            next_state = "paused"
        elif norm_action == "resume":
            next_state = "running"
        elif norm_action == "cancel":
            next_state = "cancelled"
        else:  # redirect
            next_state = "redirected"
            if redirect_target:
                session["target"] = redirect_target

        session["state"] = next_state
        session["checkpoint_saved"] = True
        session["last_reason"] = reason or f"Action '{norm_action}' applied."

        return {
            "updated": True,
            "session_id": session_id,
            "action_applied": norm_action,
            "previous_state": prev_state,
            "current_state": next_state,
            "target": session["target"],
            "checkpoint_saved": True,
            "reason": session["last_reason"],
        }