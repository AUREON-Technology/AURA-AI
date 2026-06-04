import json
import logging
from datetime import datetime
from google import genai
from google.genai import types
from config import settings

logger = logging.getLogger(__name__)

class AutomationService:
    def __init__(self):
        self.client = None
        if settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)
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
        
        if not self.client:
            logger.error("Gemini client not initialized for email analysis.")
            return {
                "category": "General",
                "summary": "Failed to parse email content due to missing API configuration.",
                "urgency": "low",
                "action_items": [],
                "should_create_event": False,
                "event_title": None,
                "event_start": None,
                "event_end": None
            }

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            data = json.loads(response.text)
            return data
        except Exception as e:
            logger.error(f"Error in Gemini email analysis: {e}")
            return {
                "category": "General",
                "summary": "Failed to parse email content due to error.",
                "urgency": "low",
                "action_items": [],
                "should_create_event": False,
                "event_title": None,
                "event_start": None,
                "event_end": None
            }

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

        if not self.client:
            logger.error("Gemini client not initialized for issue analysis.")
            return {
                "summary": "Failed to parse GitHub issue content due to missing API configuration.",
                "subtasks": [],
                "complexity": "medium",
                "suggested_priority": "medium"
            }

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            data = json.loads(response.text)
            return data
        except Exception as e:
            logger.error(f"Error in Gemini issue analysis: {e}")
            return {
                "summary": "Failed to parse GitHub issue content due to error.",
                "subtasks": [],
                "complexity": "medium",
                "suggested_priority": "medium"
            }

automation_service = AutomationService()
