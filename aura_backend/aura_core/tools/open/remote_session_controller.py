"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. A process-local dict of
session states connected to nothing: ``apply_action("pause")`` reports ``current_state: "paused"``
while the real run continues, and sessions have no owner scoping (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import time
from typing import Any, Dict, List, Optional


class RemoteSessionController:
    """State controller for pausing, resuming, cancelling, snapshotting, and redirecting agent sessions safely."""

    VALID_ACTIONS = {"pause", "resume", "cancel", "redirect", "terminate"}

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._checkpoints: Dict[str, List[Dict[str, Any]]] = {}

    def get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve session state or initialize a default running session."""
        if session_id not in self._sessions:
            now = time.time()
            self._sessions[session_id] = {
                "session_id": session_id,
                "state": "running",
                "target": None,
                "checkpoint_saved": True,
                "last_reason": None,
                "created_at": now,
                "updated_at": now,
            }
            self._checkpoints[session_id] = []
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
        elif norm_action == "terminate":
            next_state = "terminated"
        else:  # redirect
            next_state = "redirected"
            if redirect_target:
                session["target"] = redirect_target

        session["state"] = next_state
        session["updated_at"] = time.time()
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

    def save_session_checkpoint(self, session_id: str, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Record a serialized state checkpoint for session recovery and rollback."""
        session = self.get_or_create_session(session_id)
        cp_id = f"cp_{len(self._checkpoints[session_id]) + 1}_{int(time.time())}"
        checkpoint_entry = {
            "checkpoint_id": cp_id,
            "timestamp": time.time(),
            "state": session["state"],
            "data": snapshot_data,
        }
        self._checkpoints[session_id].append(checkpoint_entry)
        session["checkpoint_saved"] = True
        session["updated_at"] = time.time()

        return {
            "saved": True,
            "session_id": session_id,
            "checkpoint_id": cp_id,
            "checkpoints_count": len(self._checkpoints[session_id]),
        }

    def restore_session_checkpoint(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore session state to a previously saved checkpoint."""
        if session_id not in self._sessions or not self._checkpoints.get(session_id):
            return {"restored": False, "session_id": session_id, "error": "No checkpoints found for session."}

        history = self._checkpoints[session_id]
        target_cp = None
        if checkpoint_id:
            for cp in history:
                if cp["checkpoint_id"] == checkpoint_id:
                    target_cp = cp
                    break
        else:
            target_cp = history[-1]

        if not target_cp:
            return {"restored": False, "session_id": session_id, "error": f"Checkpoint '{checkpoint_id}' not found."}

        session = self._sessions[session_id]
        session["state"] = target_cp["state"]
        session["updated_at"] = time.time()
        session["last_reason"] = f"Restored to checkpoint {target_cp['checkpoint_id']}"

        return {
            "restored": True,
            "session_id": session_id,
            "restored_checkpoint_id": target_cp["checkpoint_id"],
            "snapshot_data": target_cp["data"],
        }

    def list_active_sessions(self, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tracked sessions with current status and elapsed runtime."""
        results: List[Dict[str, Any]] = []
        now = time.time()
        filter_norm = state_filter.lower() if state_filter else None

        for sid, s in self._sessions.items():
            if filter_norm and s["state"] != filter_norm:
                continue
            results.append({
                "session_id": sid,
                "state": s["state"],
                "target": s.get("target"),
                "uptime_seconds": round(now - s.get("created_at", now), 1),
                "checkpoints_stored": len(self._checkpoints.get(sid, [])),
                "last_reason": s.get("last_reason"),
            })

        return results

    def terminate_session(self, session_id: str, exit_reason: str = "User requested termination") -> Dict[str, Any]:
        """Safely terminate a session and release associated tracking state."""
        return self.apply_action(session_id, action="terminate", reason=exit_reason)
