import copy
import json
import threading
import time
from typing import Any, Dict, List, Optional

_MAX_SESSIONS = 1000
_MAX_CHECKPOINTS = 100
_MAX_SNAPSHOT_CHARS = 1_000_000
_MAX_TEXT = 500
_TERMINAL_STATES = ("cancelled", "terminated")

_NOTE = (
    "State record only: this controller keeps a ledger of what was asked. It does not signal, "
    "pause or stop any running session."
)


class RemoteSessionController:
    """State ledger for pausing, resuming, cancelling, snapshotting, and redirecting agent sessions.

    **This class records state; it does not control anything.** Applying ``pause`` changes a field in
    an in-memory dict -- no running agent is signalled. Every action result says so (``note``) so a
    caller cannot mistake "recorded" for "stopped". Within that scope it is strict:

    * ``cancelled`` and ``terminated`` are final: a terminated session is not silently resumed
      by ``resume`` or by restoring an older checkpoint;
    * an action on a session that was never registered (``get_or_create_session``) is refused
      instead of creating a phantom session from a typo;
    * ``checkpoint_saved`` is true only when a checkpoint really exists;
    * snapshots are deep-copied in and out, so later edits by the caller (or by whoever received the
      restored data) cannot rewrite history; sessions, checkpoints and snapshot size are capped.

    State lives on the instance, guarded by a lock; nothing is shared between instances.
    """

    VALID_ACTIONS = {"pause", "resume", "cancel", "redirect", "terminate"}

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._checkpoints: Dict[str, List[Dict[str, Any]]] = {}
        self._cp_counter: Dict[str, int] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    @staticmethod
    def _valid_id(session_id: Any) -> bool:
        return isinstance(session_id, str) and bool(session_id.strip()) and len(session_id) <= 128 \
            and session_id.isprintable()

    def get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve session state or initialize a default running session.

        Returns a copy of the session record. Raises ``ValueError`` for an invalid ``session_id``
        or when the session limit is reached (the only error this class raises).
        """
        if not self._valid_id(session_id):
            raise ValueError("session_id must be a non-empty printable string of at most 128 characters.")
        with self._lock:
            if session_id not in self._sessions:
                if len(self._sessions) >= _MAX_SESSIONS:
                    raise ValueError(f"Too many sessions (limit {_MAX_SESSIONS}).")
                now = time.time()
                self._sessions[session_id] = {
                    "session_id": session_id,
                    "state": "running",
                    "target": None,
                    "checkpoint_saved": False,
                    "last_reason": None,
                    "created_at": now,
                    "updated_at": now,
                }
                self._checkpoints[session_id] = []
                self._cp_counter[session_id] = 0
            return dict(self._sessions[session_id])

    def apply_action(
        self,
        session_id: str,
        action: str,
        reason: Optional[str] = None,
        redirect_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a session control action (pause, resume, cancel, redirect, terminate)."""
        if not isinstance(action, str):
            return {"updated": False, "session_id": session_id,
                    "error": f"action must be a string (got {type(action).__name__})."}
        norm_action = action.strip().lower()
        if norm_action not in self.VALID_ACTIONS:
            return {
                "updated": False,
                "session_id": session_id,
                "error": f"Invalid action '{action}'. Allowed: {sorted(self.VALID_ACTIONS)}",
            }
        if not self._valid_id(session_id):
            return {"updated": False, "session_id": session_id,
                    "error": "session_id must be a non-empty printable string of at most 128 characters."}
        if reason is not None and not isinstance(reason, str):
            return {"updated": False, "session_id": session_id, "error": "reason must be a string."}
        if norm_action == "redirect":
            if not isinstance(redirect_target, str) or not redirect_target.strip():
                return {"updated": False, "session_id": session_id,
                        "error": "redirect requires a non-empty redirect_target."}
            if len(redirect_target) > _MAX_TEXT:
                return {"updated": False, "session_id": session_id, "error": "redirect_target is too long."}

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return {"updated": False, "session_id": session_id,
                        "error": "Unknown session: register it with get_or_create_session first."}
            prev_state = session["state"]
            if prev_state in _TERMINAL_STATES:
                return {"updated": False, "session_id": session_id, "previous_state": prev_state,
                        "error": f"Session is {prev_state}; that is final. Start a new session instead."}

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
                session["target"] = redirect_target

            session["state"] = next_state
            session["updated_at"] = time.time()
            session["checkpoint_saved"] = bool(self._checkpoints.get(session_id))
            session["last_reason"] = (reason or f"Action '{norm_action}' applied.")[:_MAX_TEXT]

            return {
                "updated": True,
                "session_id": session_id,
                "action_applied": norm_action,
                "previous_state": prev_state,
                "current_state": next_state,
                "target": session["target"],
                "checkpoint_saved": session["checkpoint_saved"],
                "reason": session["last_reason"],
                "note": _NOTE,
            }

    def save_session_checkpoint(self, session_id: str, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Record a copy of ``snapshot_data`` (a JSON-like dict) for later restore."""
        if not self._valid_id(session_id):
            return {"saved": False, "session_id": session_id,
                    "error": "session_id must be a non-empty printable string of at most 128 characters."}
        if not isinstance(snapshot_data, dict):
            return {"saved": False, "session_id": session_id, "error": "snapshot_data must be a dict."}
        try:
            size = len(json.dumps(snapshot_data, default=repr))
            snapshot = copy.deepcopy(snapshot_data)
        except (TypeError, ValueError, RecursionError) as exc:
            return {"saved": False, "session_id": session_id,
                    "error": f"snapshot_data could not be copied ({type(exc).__name__})."}
        if size > _MAX_SNAPSHOT_CHARS:
            return {"saved": False, "session_id": session_id,
                    "error": f"snapshot_data is too large (limit {_MAX_SNAPSHOT_CHARS} characters)."}

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return {"saved": False, "session_id": session_id,
                        "error": "Unknown session: register it with get_or_create_session first."}
            if session["state"] in _TERMINAL_STATES:
                return {"saved": False, "session_id": session_id,
                        "error": f"Session is {session['state']}; no further checkpoints are recorded."}
            self._cp_counter[session_id] += 1
            cp_id = f"cp_{self._cp_counter[session_id]}_{int(time.time())}"
            history = self._checkpoints[session_id]
            history.append({
                "checkpoint_id": cp_id,
                "timestamp": time.time(),
                "state": session["state"],
                "data": snapshot,
            })
            if len(history) > _MAX_CHECKPOINTS:
                del history[: len(history) - _MAX_CHECKPOINTS]
            session["checkpoint_saved"] = True
            session["updated_at"] = time.time()

            return {
                "saved": True,
                "session_id": session_id,
                "checkpoint_id": cp_id,
                "checkpoints_count": len(history),
            }

    def restore_session_checkpoint(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore the recorded state (and return the snapshot) of a checkpoint; the latest by default."""
        with self._lock:
            if not isinstance(session_id, str) or session_id not in self._sessions \
                    or not self._checkpoints.get(session_id):
                return {"restored": False, "session_id": session_id, "error": "No checkpoints found for session."}
            session = self._sessions[session_id]
            if session["state"] in _TERMINAL_STATES:
                return {"restored": False, "session_id": session_id,
                        "error": f"Session is {session['state']}; a final state cannot be restored over."}

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
                return {"restored": False, "session_id": session_id,
                        "error": f"Checkpoint '{checkpoint_id}' not found."}

            session["state"] = target_cp["state"]
            session["updated_at"] = time.time()
            session["last_reason"] = f"Restored to checkpoint {target_cp['checkpoint_id']}"

            return {
                "restored": True,
                "session_id": session_id,
                "restored_checkpoint_id": target_cp["checkpoint_id"],
                "snapshot_data": copy.deepcopy(target_cp["data"]),
                "note": _NOTE,
            }

    def list_active_sessions(self, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tracked sessions (all states unless ``state_filter`` is given) with elapsed runtime."""
        results: List[Dict[str, Any]] = []
        now = time.time()
        if state_filter is not None and not isinstance(state_filter, str):
            return results
        filter_norm = state_filter.strip().lower() if state_filter else None

        with self._lock:
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
        """Mark a session ``terminated`` (final). Its checkpoints are kept for audit, not released."""
        return self.apply_action(session_id, action="terminate", reason=exit_reason)
