import json
import logging
from datetime import datetime
from config import settings
from services.ollama import ollama_service

logger = logging.getLogger(__name__)

class AutomationService:
    """Structured email and issue analysis, running entirely on our own inference host.

    There is no client to construct and no key to check: `ollama_service` is a module singleton
    pointing at settings.ollama_host, and `generate_json` returns None for every failure mode --
    unreachable server, non-200, unparseable output. The previous `if not self.client` guards
    existed to detect a missing API key, a condition that no longer exists, so they are gone; the
    default dictionaries they returned are now reached through the single None check in each
    method.
    """
    def analyze_email(self, subject: str, sender: str, body: str) -> dict:
        """Analyzes email subject and body using Gemini to return structured JSON data."""
        prompt = f"""
        Analyze the following email and extract structured metadata and action items.
        Sender: {sender}
        Subject: {subject}
        Body:
        {body}

        Today's Date: {datetime.now().strftime('%Y-%m-%d %A')}

        Respond ONLY with a JSON object containing the following keys:
        - "category": String. Must be one of: "Academic", "Tasks", "Finance", "Health", "Social", "Spam", "General".
        - "summary": String. A brief 1-2 sentence summary of the email.
        - "urgency": String. "high", "medium", or "low".
        - "action_items": List of objects. Each object should have:
            - "title": String. The specific task title.
            - "due_date": String or null. Format YYYY-MM-DD if a deadline is mentioned in the text (infer relative to Today's Date).
        - "should_create_event": Boolean. True if the email specifies a specific meeting, calendar appointment, class, or deadline event.
        - "event_title": String or null. Title for calendar event.
        - "event_start": String or null. Start datetime in format YYYY-MM-DDTHH:MM:SS (local time).
        - "event_end": String or null. End datetime in format YYYY-MM-DDTHH:MM:SS (local time).
        """
        
        fallback = {
            "category": "General",
            "summary": "Email analysis unavailable — local inference did not return a result.",
            "urgency": "low",
            "action_items": [],
            "should_create_event": False,
            "event_title": None,
            "event_start": None,
            "event_end": None
        }

        try:
            # The budget check stays. It no longer guards a bill -- local inference is free at the
            # margin -- but it still guards the single scarcest resource on the box: model
            # concurrency. An unbounded fan-out of analysis calls starves interactive chat.
            from services.budget import increment_and_check_gemini_budget, GeminiBudgetExceededException
            increment_and_check_gemini_budget()

            data = ollama_service.generate_json(
                prompt,
                system_instruction=(
                    "You extract structured metadata from email. Reply with JSON only, matching "
                    "the keys the prompt describes."
                ),
            )
            if data is None:
                logger.warning("Local inference returned no usable email analysis.")
                return fallback
            return data
        except GeminiBudgetExceededException:
            raise
        except Exception as e:
            logger.error(f"Error in local email analysis: {e}")
            return fallback

    def analyze_github_issue(self, title: str, body: str) -> dict:
        """Analyzes a GitHub issue and extracts checkable subtask steps and metadata."""
        prompt = f"""
        Analyze the following GitHub issue and extract actionable coding subtasks and metadata.
        Issue Title: {title}
        Issue Body:
        {body}

        Respond ONLY with a JSON object containing the following keys:
        - "summary": String. A brief description of the issue.
        - "subtasks": List of strings. Actionable coding checklist items required to resolve this issue.
        - "complexity": String. "low", "medium", or "high".
        - "suggested_priority": String. "low", "medium", or "high".
        """

        fallback = {
            "summary": "Issue analysis unavailable — local inference did not return a result.",
            "subtasks": [],
            "complexity": "medium",
            "suggested_priority": "medium"
        }

        try:
            from services.budget import increment_and_check_gemini_budget, GeminiBudgetExceededException
            increment_and_check_gemini_budget()

            data = ollama_service.generate_json(
                prompt,
                system_instruction=(
                    "You break issues into subtasks and estimate complexity. Reply with JSON "
                    "only, matching the keys the prompt describes."
                ),
            )
            if data is None:
                logger.warning("Local inference returned no usable issue analysis.")
                return fallback
            return data
        except GeminiBudgetExceededException:
            raise
        except Exception as e:
            logger.error(f"Error in local issue analysis: {e}")
            return fallback

automation_service = AutomationService()
