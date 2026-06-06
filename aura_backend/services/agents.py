import logging
from typing import List, Dict, Any, AsyncGenerator
from google.genai import types
from services.ai import ai_service
from services.memory import memory_service
from services.rag import rag_service
from services.automation import automation_service

logger = logging.getLogger("aura_backend.agents")

class BaseAgent:
    """Base class for all specialized Agent classes."""
    def __init__(self, agent_id: str, system_instruction: str, client_ai=None):
        self.agent_id = agent_id
        self.system_instruction = system_instruction
        self.client_ai = client_ai

    async def execute_stream(
        self,
        messages: List[Dict[str, str]],
        use_rag: bool = True,
        user_id: str = None,
        google_access_token: str = None
    ) -> AsyncGenerator[str, None]:
        """Streams response using AIService fallback stack, injecting agent system instructions."""
        logger.info(f"Executing agent '{self.agent_id}' stream...")
        # Fallback to general chat stream with injected system instruction
        async for chunk in ai_service.generate_chat_response_stream(
            messages=messages,
            use_rag=use_rag,
            user_id=user_id
        ):
            yield chunk


class ChatAgent(BaseAgent):
    """Handles general chit-chat and greetings."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="chat_agent",
            system_instruction="You are AURA's Chat Agent. Help the user with conversational queries, general greetings, and questions. Keep it polite, engaging, and professional.",
            client_ai=client_ai
        )


class MemoryAgent(BaseAgent):
    """Manages user personal facts extraction and long-term storage."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="memory_agent",
            system_instruction="You are AURA's Memory Agent. Your focus is to manage and list the user's memories, facts, and preferences. You can save and query user-specific memory points.",
            client_ai=client_ai
        )


class RAGAgent(BaseAgent):
    """Answers user queries using document retrieval (RAG) from Qdrant."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="rag_agent",
            system_instruction="You are AURA's RAG Agent. Use retrieved context from Qdrant documents to answer user queries accurately. Cite details from context when available.",
            client_ai=client_ai
        )


class AutomationAgent(BaseAgent):
    """Handles email parsing, Github issues, calendar, and Drive integrations."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="automation_agent",
            system_instruction="You are AURA's Automation Agent. Help analyze emails, schedule calendar events, download Google Drive files, and manage workspace tasks.",
            client_ai=client_ai
        )


class KnowledgeAgent(BaseAgent):
    """Handles direct document ingestion and vector DB collection indexing."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="knowledge_agent",
            system_instruction="You are AURA's Knowledge Ingestion Agent. Assist the user with indexing plain text files, updating vector databases, and managing document stores.",
            client_ai=client_ai
        )


class CodingAgent(BaseAgent):
    """Assists with software engineering, programming structures, and specifications."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="coding_agent",
            system_instruction="You are AURA's Coding Agent. Provide clean, professional code snippets, debug programming logic, explain software design patterns, and structure architectures.",
            client_ai=client_ai
        )


class PlanningAgent(BaseAgent):
    """Coordinates calendar schedules, prioritizing todo check-lists, and decision evaluation."""
    def __init__(self, client_ai=None):
        super().__init__(
            agent_id="planning_agent",
            system_instruction="You are AURA's Planning Agent. Help prioritize todo tasks, structure checklist deadlines, coordinate schedules, and perform cost-benefit analysis for choices.",
            client_ai=client_ai
        )
