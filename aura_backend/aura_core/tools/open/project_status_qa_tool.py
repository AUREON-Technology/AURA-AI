"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Keyword search and
summaries over an ``execution_history`` the caller must supply; no AURA engine produces that
shape. Not worth the planner prompt.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import re
import time
from typing import Any, Dict, List, Optional


class ProjectStatusQaTool:
    """Evidence-backed QA helper for querying project execution history, stages, and errors."""

    @staticmethod
    def query_history(
        query: str,
        execution_history: List[Dict[str, Any]],
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Search execution_history records matching terms from query."""
        q_lower = query.strip().lower()
        terms = [t for t in q_lower.split() if len(t) > 1]
        matched: List[Dict[str, Any]] = []

        for record in execution_history:
            stage = str(record.get("stage", "")).lower()
            status = str(record.get("status", "")).lower()
            message = str(record.get("message", "")).lower()
            combined = f"{stage} {status} {message}"

            if q_lower in combined or any(term in combined for term in terms):
                matched.append(dict(record))

        evidence = matched[: max(0, limit)]
        return {
            "query": query,
            "matches_found": len(matched),
            "returned_count": len(evidence),
            "evidence": evidence,
            "direct_answer": f"Found {len(matched)} matching execution records.",
        }

    @staticmethod
    def summarize_stage_progress(execution_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze execution history to summarize stage progression, status, and bottlenecks."""
        if not execution_history:
            return {
                "total_events": 0,
                "stages": {},
                "active_stage": None,
                "overall_status": "UNKNOWN",
                "success_rate": 0.0,
            }

        stages: Dict[str, Dict[str, Any]] = {}
        total_events = len(execution_history)
        success_count = 0
        failure_count = 0

        for rec in execution_history:
            stg = str(rec.get("stage", "default")).strip()
            stat = str(rec.get("status", "running")).strip().upper()
            if stg not in stages:
                stages[stg] = {"events": 0, "status": stat, "last_message": rec.get("message", "")}
            stages[stg]["events"] += 1
            stages[stg]["status"] = stat
            stages[stg]["last_message"] = rec.get("message", "")

            if stat in ("SUCCESS", "PASSED", "COMPLETED", "DONE"):
                success_count += 1
            elif stat in ("FAILED", "ERROR", "FAILURE"):
                failure_count += 1

        active_stage = None
        for stg, info in stages.items():
            if info["status"] in ("RUNNING", "IN_PROGRESS", "PENDING"):
                active_stage = stg
                break
        if not active_stage and stages:
            active_stage = list(stages.keys())[-1]

        overall_status = "FAILED" if failure_count > 0 else ("COMPLETED" if success_count > 0 else "RUNNING")
        rate = round((success_count / total_events) * 100.0, 2) if total_events > 0 else 0.0

        return {
            "total_events": total_events,
            "stages_count": len(stages),
            "stages": stages,
            "active_stage": active_stage,
            "overall_status": overall_status,
            "success_rate": rate,
            "failure_count": failure_count,
        }

    @staticmethod
    def get_error_digest(execution_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract and categorize all failure events, error messages, and root causes."""
        errors: List[Dict[str, Any]] = []
        error_categories: Dict[str, int] = {}

        for rec in execution_history:
            stat = str(rec.get("status", "")).upper()
            msg = str(rec.get("message", ""))
            err_kind = rec.get("error_kind") or rec.get("error")

            is_err = stat in ("FAILED", "ERROR", "FAILURE") or "error" in msg.lower() or "exception" in msg.lower()
            if is_err:
                cat = "GenericError"
                if "timeout" in msg.lower():
                    cat = "TimeoutError"
                elif "syntax" in msg.lower():
                    cat = "SyntaxError"
                elif "assert" in msg.lower():
                    cat = "AssertionError"
                elif "import" in msg.lower():
                    cat = "ImportError"
                elif "permission" in msg.lower():
                    cat = "PermissionError"
                elif err_kind:
                    cat = str(err_kind)

                error_categories[cat] = error_categories.get(cat, 0) + 1
                errors.append({
                    "stage": rec.get("stage", "unknown"),
                    "category": cat,
                    "message": msg,
                    "timestamp": rec.get("timestamp"),
                    "raw_record": rec,
                })

        return {
            "has_errors": len(errors) > 0,
            "error_count": len(errors),
            "error_categories": error_categories,
            "errors": errors,
            "primary_bottleneck": max(error_categories, key=error_categories.get) if error_categories else None,
        }

    @staticmethod
    def reconstruct_timeline(execution_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reconstruct chronological milestone timeline with relative duration offsets."""
        if not execution_history:
            return []

        # Sort by timestamp if available
        def _get_ts(rec: Dict[str, Any]) -> float:
            ts = rec.get("timestamp")
            if isinstance(ts, (int, float)):
                return float(ts)
            return 0.0

        sorted_records = sorted(execution_history, key=_get_ts)
        start_ts = _get_ts(sorted_records[0]) if sorted_records else 0.0

        timeline = []
        for idx, rec in enumerate(sorted_records, start=1):
            cur_ts = _get_ts(rec)
            delta_s = round(cur_ts - start_ts, 2) if (start_ts > 0 and cur_ts > 0) else 0.0
            timeline.append({
                "step": idx,
                "stage": rec.get("stage", "unspecified"),
                "status": rec.get("status", "running"),
                "message": rec.get("message", ""),
                "offset_seconds": delta_s,
                "timestamp": cur_ts,
            })
        return timeline

    @classmethod
    def answer_status_question(
        cls,
        question: str,
        execution_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Answer high-level natural language questions about task progress, errors, or stages."""
        q_lower = question.lower()
        progress = cls.summarize_stage_progress(execution_history)
        errs = cls.get_error_digest(execution_history)

        if any(w in q_lower for w in ("error", "fail", "broken", "issue", "bug", "why")):
            if errs["has_errors"]:
                ans = f"Project encountered {errs['error_count']} error(s). Primary cause: {errs['primary_bottleneck']} ({errs['errors'][0]['message']})."
            else:
                ans = "No execution errors or failures detected in recorded history."
            intent = "error_query"
        elif any(w in q_lower for w in ("stage", "where", "active", "current")):
            ans = f"Active stage is '{progress['active_stage']}'. Overall status is {progress['overall_status']} across {progress['stages_count']} stage(s)."
            intent = "stage_query"
        elif any(w in q_lower for w in ("done", "finish", "complete", "progress", "rate", "percent")):
            ans = f"Progress is {progress['overall_status']} with {progress['success_rate']}% success rate across {progress['total_events']} events."
            intent = "progress_query"
        else:
            search_res = cls.query_history(question, execution_history, limit=3)
            ans = search_res["direct_answer"]
            intent = "general_search"

        return {
            "question": question,
            "intent": intent,
            "answer": ans,
            "stage_summary": progress,
            "error_summary": errs,
        }
