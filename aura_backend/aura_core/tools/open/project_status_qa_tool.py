"""Answer questions about a project's execution history (a list of ``{stage, status, message, ...}``).

Text/data only: the history is passed in as records, so there is no file or PDF input here. (A PDF
report would have to be read through ``_input`` and turned into records by the caller.)

Rules this module holds to, because a status answer is exactly what people stop double-checking:

* No history is never "no errors". It is "nothing recorded".
* ``COMPLETED`` means every stage's latest event is a success. One finished stage out of ten is
  ``RUNNING``.
* A message that merely *mentions* errors ("0 errors", "added error handling") on a successful event
  is not an error.
"""

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_SUCCESS = {"SUCCESS", "SUCCEEDED", "PASSED", "PASS", "COMPLETED", "COMPLETE", "DONE", "OK", "FINISHED"}
_FAILURE = {"FAILED", "FAIL", "ERROR", "FAILURE", "TIMEOUT", "TIMED_OUT", "CANCELLED", "CANCELED", "ABORTED", "CRASHED"}
_ACTIVE = {"RUNNING", "IN_PROGRESS", "PENDING", "STARTED", "QUEUED"}

_STOPWORDS = {
    "the", "is", "are", "was", "were", "what", "which", "who", "how", "why", "when", "where", "did", "does",
    "do", "has", "have", "had", "and", "for", "with", "this", "that", "from", "any", "all", "about", "there",
    "of", "to", "in", "on", "at", "it", "a", "an", "me", "my", "our", "show", "tell", "give", "records", "record",
}
_ERROR_MSG_RE = re.compile(r"\b(?:errors?|exceptions?|tracebacks?|failed|failure|failures|crash(?:ed)?)\b|خطأ|أخطاء|فشل|استثناء", re.I)
_NEGATED_ERROR_RE = re.compile(
    r"\b(?:no|zero|0|without|none|not\s+any)\s+(?:\w+\s+)?(?:errors?|exceptions?|failures?|tracebacks?)\b", re.I
)
_MAX_ERRORS = 100


def _records(history: Any) -> Tuple[List[Dict[str, Any]], int]:
    """The dict records of ``history`` and how many entries were not records (and so skipped)."""
    if history is None:
        return [], 0
    if isinstance(history, dict):
        history = [history]
    if isinstance(history, (str, bytes)) or not hasattr(history, "__iter__"):
        return [], 1
    recs, skipped = [], 0
    for r in history:
        if isinstance(r, dict):
            recs.append(r)
        else:
            skipped += 1
    return recs, skipped


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if t]


def _status(rec: Dict[str, Any], default: str = "") -> str:
    raw = rec.get("status")
    return str(raw).strip().upper().replace(" ", "_").replace("-", "_") if raw not in (None, "") else default


def _ts(value: Any) -> Optional[float]:
    """Epoch seconds from a number (seconds or milliseconds) or an ISO-8601 string; else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _is_error_record(rec: Dict[str, Any]) -> bool:
    stat = _status(rec)
    if stat in _FAILURE:
        return True
    if stat in _SUCCESS:
        return False  # a success that mentions errors ("0 errors", "error handling added") is not one
    msg = str(rec.get("message", ""))
    return bool(_ERROR_MSG_RE.search(msg)) and not _NEGATED_ERROR_RE.search(msg)


class ProjectStatusQaTool:
    """Evidence-backed QA helper for querying project execution history, stages, and errors."""

    @staticmethod
    def query_history(
        query: str,
        execution_history: List[Dict[str, Any]],
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Search execution_history records for the words of ``query`` (stage, status, message).

        Stop-words and one-letter words are ignored, so "what is the status" does not match every
        record. Records matching more distinct words come first. An empty query matches nothing.
        """
        recs, skipped = _records(execution_history)
        q = query if isinstance(query, str) else ("" if query is None else str(query))
        terms = [t for t in dict.fromkeys(_tokens(q)) if len(t) > 1 and t not in _STOPWORDS]
        scored: List[Tuple[int, int, Dict[str, Any]]] = []

        if terms:
            for idx, record in enumerate(recs):
                hay = _tokens(f"{record.get('stage', '')} {record.get('status', '')} {record.get('message', '')}")
                score = sum(1 for term in terms if any(term in tok for tok in hay))
                if score:
                    scored.append((-score, idx, record))
        scored.sort(key=lambda x: (x[0], x[1]))
        matched = [dict(r) for _s, _i, r in scored]

        try:
            n = max(0, int(limit))
        except (TypeError, ValueError, OverflowError):
            n = 5
        evidence = matched[:n]
        result: Dict[str, Any] = {
            "query": query,
            "matches_found": len(matched),
            "returned_count": len(evidence),
            "evidence": evidence,
            "direct_answer": f"Found {len(matched)} matching execution records.",
        }
        warnings: List[str] = []
        if not terms:
            warnings.append("The query has no searchable words, so nothing was matched.")
        if not recs:
            warnings.append("The execution history is empty.")
        if skipped:
            warnings.append(f"{skipped} history entr{'y was' if skipped == 1 else 'ies were'} not records and skipped.")
        if warnings:
            result["warnings"] = warnings
        return result

    @staticmethod
    def summarize_stage_progress(execution_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze execution history to summarize stage progression, status, and bottlenecks.

        Overall status looks at each stage's *latest* event: any failed stage -> ``FAILED``; any
        stage still running/pending/unknown -> ``RUNNING``; every stage succeeded -> ``COMPLETED``.
        """
        recs, skipped = _records(execution_history)
        if not recs:
            out: Dict[str, Any] = {
                "total_events": 0,
                "stages_count": 0,
                "stages": {},
                "active_stage": None,
                "overall_status": "UNKNOWN",
                "success_rate": 0.0,
                "failure_count": 0,
            }
            if skipped:
                out["skipped_records"] = skipped
            return out

        stages: Dict[str, Dict[str, Any]] = {}
        total_events = len(recs)
        success_count = 0
        failure_count = 0

        for rec in recs:
            stg = str(rec.get("stage") or "default").strip() or "default"
            stat = _status(rec, "RUNNING")
            if stg not in stages:
                stages[stg] = {"events": 0, "status": stat, "last_message": rec.get("message", "")}
            stages[stg]["events"] += 1
            stages[stg]["status"] = stat
            stages[stg]["last_message"] = rec.get("message", "")

            if stat in _SUCCESS:
                success_count += 1
            elif stat in _FAILURE:
                failure_count += 1

        active_stage = None
        for stg, info in stages.items():
            if info["status"] in _ACTIVE:
                active_stage = stg
                break
        if not active_stage and stages:
            active_stage = list(stages.keys())[-1]

        latest = [info["status"] for info in stages.values()]
        if any(s in _FAILURE for s in latest):
            overall_status = "FAILED"
        elif all(s in _SUCCESS for s in latest):
            overall_status = "COMPLETED"
        else:
            overall_status = "RUNNING"
        rate = round((success_count / total_events) * 100.0, 2)

        out = {
            "total_events": total_events,
            "stages_count": len(stages),
            "stages": stages,
            "active_stage": active_stage,
            "overall_status": overall_status,
            "success_rate": rate,
            "failure_count": failure_count,
        }
        if skipped:
            out["skipped_records"] = skipped
        return out

    @staticmethod
    def get_error_digest(execution_history: List[Dict[str, Any]], max_errors: int = _MAX_ERRORS) -> Dict[str, Any]:
        """Extract and categorize failure events and error messages.

        ``error_count`` is exact; ``errors`` holds at most ``max_errors`` entries and
        ``errors_truncated`` says when more existed.
        """
        recs, skipped = _records(execution_history)
        errors: List[Dict[str, Any]] = []
        error_categories: Dict[str, int] = {}
        error_count = 0
        try:
            cap = max(0, int(max_errors))
        except (TypeError, ValueError, OverflowError):
            cap = _MAX_ERRORS

        for rec in recs:
            if not _is_error_record(rec):
                continue
            msg = str(rec.get("message", ""))
            low = msg.lower()
            err_kind = rec.get("error_kind") or rec.get("error")

            if err_kind:
                cat = str(err_kind)
            elif re.search(r"\btime(?:d)?[ _-]?out\b|انتهت المهلة", low):
                cat = "TimeoutError"
            elif re.search(r"\bsyntax\b", low):
                cat = "SyntaxError"
            elif re.search(r"\bassert(?:ion)?", low):
                cat = "AssertionError"
            elif re.search(r"\bimport(?:error)?\b", low):
                cat = "ImportError"
            elif re.search(r"\bpermission\b", low):
                cat = "PermissionError"
            else:
                cat = "GenericError"

            error_count += 1
            error_categories[cat] = error_categories.get(cat, 0) + 1
            if len(errors) < cap:
                errors.append({
                    "stage": rec.get("stage", "unknown"),
                    "category": cat,
                    "message": msg,
                    "timestamp": rec.get("timestamp"),
                    "raw_record": rec,
                })

        out: Dict[str, Any] = {
            "has_errors": error_count > 0,
            "error_count": error_count,
            "error_categories": error_categories,
            "errors": errors,
            "errors_truncated": error_count > len(errors),
            "primary_bottleneck": max(error_categories, key=error_categories.get) if error_categories else None,
            "records_examined": len(recs),
        }
        if skipped:
            out["skipped_records"] = skipped
        return out

    @staticmethod
    def reconstruct_timeline(execution_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reconstruct a chronological timeline with offsets (seconds) from the earliest known timestamp.

        Timestamps may be epoch seconds, epoch milliseconds or ISO-8601 strings. A record without a
        usable timestamp keeps its place next to the record before it and is marked
        ``timestamp_known: False`` (its ``offset_seconds`` is then ``0.0`` and means "unknown").
        """
        recs, _skipped = _records(execution_history)
        if not recs:
            return []

        keyed: List[Tuple[float, int, Dict[str, Any], Optional[float]]] = []
        carry = 0.0
        for idx, rec in enumerate(recs):
            known = _ts(rec.get("timestamp"))
            if known is not None:
                carry = known
            keyed.append((carry, idx, rec, known))
        keyed.sort(key=lambda x: (x[0], x[1]))

        known_values = [k[3] for k in keyed if k[3] is not None]
        start_ts = min(known_values) if known_values else None

        timeline = []
        for step, (_eff, _idx, rec, known) in enumerate(keyed, start=1):
            delta_s = round(known - start_ts, 2) if (known is not None and start_ts is not None) else 0.0
            timeline.append({
                "step": step,
                "stage": rec.get("stage", "unspecified"),
                "status": rec.get("status", "running"),
                "message": rec.get("message", ""),
                "offset_seconds": delta_s,
                "timestamp": known if known is not None else 0.0,
                "timestamp_known": known is not None,
            })
        return timeline

    @classmethod
    def answer_status_question(
        cls,
        question: str,
        execution_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Answer high-level questions about task progress, errors, or stages (English and Arabic)."""
        q_lower = (question if isinstance(question, str) else ("" if question is None else str(question))).lower()
        recs, _skipped = _records(execution_history)
        progress = cls.summarize_stage_progress(execution_history)
        errs = cls.get_error_digest(execution_history)

        def has(*words: str) -> bool:
            return any(re.search(rf"\b{re.escape(w)}", q_lower) for w in words if w.isascii()) or any(
                w in q_lower for w in words if not w.isascii()
            )

        if has("error", "fail", "broken", "issue", "bug", "why", "خطأ", "أخطاء", "فشل", "مشكلة"):
            intent = "error_query"
            if not recs:
                ans = "No execution history was recorded, so nothing can be said about errors."
            elif errs["has_errors"]:
                first = errs["errors"][0]["message"] if errs["errors"] else ""
                ans = (
                    f"Project encountered {errs['error_count']} error(s). Most frequent category: "
                    f"{errs['primary_bottleneck']}. First error: {first}"
                )
            else:
                ans = "No execution errors or failures detected in recorded history."
        elif has("stage", "where", "active", "current", "مرحلة", "المرحلة"):
            intent = "stage_query"
            if not recs:
                ans = "No execution history was recorded, so there is no active stage."
            else:
                ans = (
                    f"Active stage is '{progress['active_stage']}'. Overall status is {progress['overall_status']} "
                    f"across {progress['stages_count']} stage(s)."
                )
        elif has("done", "finish", "complete", "progress", "rate", "percent", "اكتمل", "انتهى", "نسبة", "تقدم"):
            intent = "progress_query"
            if not recs:
                ans = "No execution history was recorded, so progress is unknown."
            else:
                ans = (
                    f"Progress is {progress['overall_status']} with {progress['success_rate']}% success rate "
                    f"across {progress['total_events']} events."
                )
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
