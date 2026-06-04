import json
import logging
from typing import List, Dict, Any, AsyncGenerator
from google import genai
from google.genai import types
from config import settings
from services.memory import memory_service
from services.rag import rag_service

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = {
    "orchestrator": "You are AURA (Autonomous Utility & Retrieval Assistant), a premium Personal AI Operating System and Executive Assistant. Your tone is professional, intelligent, and refined. When in orchestrator mode, help the user with general queries, and delegate specialized tasks to your specialized personas. Keep responses concise.",
    "task_planner": "You are AURA's Task Planner. Help the user prioritize tasks, outline subtask checklists, and manage schedules. Suggest tasks whenever appropriate.",
    "finance_advisor": "You are AURA's Finance Advisor. Analyze expenses, suggest budgets, and provide smart financial recommendations. Focus on helping the user stay within their means.",
    "health_companion": "You are AURA's Health Companion. Help track water, sleep, calories, weight, and exercise. Offer encouraging, evidence-based wellness tips.",
    "academic_coach": "You are AURA's Academic Coach. Assist with software engineering specifications, coding structures, learning topics, study guides, and flashcards.",
    "research_agent": "You are AURA's Research Agent. Help the user gather facts, summarize articles or documents, analyze web contents, and perform structured knowledge synthesis. Focus on objective, logical, and well-cited information retrieval.",
    "decision_agent": "You are AURA's Decision Agent. Help the user evaluate multiple options, perform risk assessments, structure cost-benefit analyses, and make logical choices. Outline trade-offs clearly for any decision."
}

ACTION_INSTRUCTIONS = """
If the user's request explicitly or implicitly requires creating, logging, scheduling, or tracking items, you must propose those actions to the client.
To propose actions, print your text response first, and then at the very end of your response, print the exact marker '[ACTIONS]' followed by a JSON array of actions.
Do not format the JSON in markdown code blocks. Keep it raw.
Allowed Actions:
1. {"type": "add_task", "params": {"title": "string", "priority": "low/medium/high", "category": "personal/academic/client/project", "dueDate": "YYYY-MM-DD or null"}}
2. {"type": "add_event", "params": {"title": "string", "startTime": "YYYY-MM-DDTHH:MM:SS", "endTime": "YYYY-MM-DDTHH:MM:SS or null", "description": "string or null"}}
3. {"type": "add_expense", "params": {"amount": float, "category": "string", "description": "string or null"}}
4. {"type": "add_income", "params": {"amount": float, "category": "string", "description": "string or null"}}
5. {"type": "log_health", "params": {"waterIntakeMl": int or null, "sleepMinutes": int or null, "caloriesConsumed": int or null, "exerciseMinutes": int or null, "weight": float or null}}

Example trailing format:
Here is your text response.
[ACTIONS]
[{"type": "add_task", "params": {"title": "Complete RAG testing", "priority": "high", "category": "project"}}]
"""

class AgentOrchestrator:
    def __init__(self):
        self.client = None
        if settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)

    def classify_intent(self, user_message: str) -> str:
        """Classifies user intent to route message to the appropriate specialized agent."""
        prompt = f"""
        Classify the following user message to determine if it should be handled by a specialized assistant or the general orchestrator.
        Message: "{user_message}"

        Choose exactly one of these categories:
        - "task_planner": For managing tasks, to-dos, checklists, and scheduling.
        - "finance_advisor": For expenses, income, budgeting, and financial planning.
        - "health_companion": For sleeping habits, water logging, workout sessions, calories, and physical stats.
        - "academic_coach": For studies, university, software development spec drafting, coding concepts, or research.
        - "research_agent": For web search, gathering facts, summarizing documents, synthesising information.
        - "decision_agent": For choices, cost-benefit analysis, options evaluation, trade-offs, and risk analysis.
        - "orchestrator": For general conversation, greeting, settings, or if it doesn't fit the above.

        Respond with ONLY the category string. Do not add explanation or markdown.
        """

        # 1. Try Gemini
        if self.client:
            try:
                response = self.client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt
                )
                intent = response.text.strip().lower()
                if intent in DEFAULT_INSTRUCTIONS:
                    return intent
            except Exception as e:
                logger.error(f"Intent classification via Gemini failed: {e}")

        # 2. Try local Ollama
        try:
            import httpx
            payload = {
                "model": settings.ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0}
            }
            response = httpx.post(f"{settings.ollama_host.rstrip('/')}/api/chat", json=payload, timeout=2.5)
            if response.status_code == 200:
                intent = response.json().get("message", {}).get("content", "").strip().lower()
                intent = intent.replace('"', '').replace("'", "").strip()
                for key in DEFAULT_INSTRUCTIONS:
                    if key in intent:
                        return key
        except Exception as e:
            logger.error(f"Intent classification via Ollama failed: {e}")

        # 3. Heuristic Fallback
        msg_lower = user_message.lower()
        if any(k in msg_lower for k in ["task", "todo", "schedule", "remind"]):
            return "task_planner"
        elif any(k in msg_lower for k in ["expense", "income", "money", "spent", "budget"]):
            return "finance_advisor"
        elif any(k in msg_lower for k in ["water", "sleep", "calorie", "workout", "weight"]):
            return "health_companion"
        elif any(k in msg_lower for k in ["study", "course", "exam", "code", "lecture"]):
            return "academic_coach"
        elif any(k in msg_lower for k in ["search", "google", "summarize", "find facts", "research"]):
            return "research_agent"
        elif any(k in msg_lower for k in ["decide", "decision", "choose", "compare", "risk", "trade-off", "cost-benefit"]):
            return "decision_agent"
        return "orchestrator"

    async def execute_agent_stream(
        self,
        agent_id: str,
        messages: List[Dict[str, str]],
        custom_instructions: Dict[str, str] = None,
        use_rag: bool = True,
        user_id: str = None
    ) -> AsyncGenerator[str, None]:
        """Streams response for selected agent. If Gemini is offline, falls back to Ollama; if Ollama is also offline, uses mocks."""
        last_user_message = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), 
            ""
        )

        # 1. Automatic routing if selected agent is orchestrator
        active_agent = agent_id
        if agent_id == "orchestrator" and last_user_message:
            active_agent = self.classify_intent(last_user_message)
            logger.info(f"Orchestrator routed message to agent: {active_agent}")

        # Get system instruction
        system_instruction = DEFAULT_INSTRUCTIONS.get(active_agent, DEFAULT_INSTRUCTIONS["orchestrator"])
        if custom_instructions and active_agent in custom_instructions and custom_instructions[active_agent]:
            system_instruction = custom_instructions[active_agent]
            logger.info(f"Using custom instructions for active agent: {active_agent}")

        # Inject tool action parameters
        system_instruction += "\n\n" + ACTION_INSTRUCTIONS

        # 2. Retrieve memories & RAG context
        memory_context = ""
        rag_context = ""
        
        if last_user_message:
            # Personal Memory retrieval
            try:
                memories = memory_service.retrieve_relevant_memories(last_user_message, limit=3, user_id=user_id)
                if memories:
                    memory_context = "\n- " + "\n- ".join([m["content"] for m in memories])
                    logger.info(f"Retrieved {len(memories)} memory contexts.")
            except Exception as e:
                logger.warning(f"Memory retrieval bypassed in stream: {e}")
            
            # Standard doc RAG retrieval (exclude memories from normal RAG if tagged, Qdrant client handles this via type tag filters)
            if use_rag:
                try:
                    search_results = rag_service.query_knowledge(last_user_message, limit=2, user_id=user_id)
                    if search_results:
                        rag_context = "\nRelevant Knowledge Base Chunks:\n"
                        for idx, res in enumerate(search_results):
                            # Filter out memories from general RAG just in case
                            if res.get("metadata", {}).get("type") == "user_memory":
                                continue
                            rag_context += f"--- Chunk {idx + 1}:\n{res['content']}\n"
                except Exception as e:
                    logger.warning(f"RAG retrieval bypassed in stream: {e}")

        # 3. Assemble full system context
        final_system_instruction = system_instruction
        if memory_context or rag_context:
            final_system_instruction += "\n\nAdditional Context:\n"
            if memory_context:
                final_system_instruction += f"Things AURA remembers about the user:{memory_context}\n"
            if rag_context:
                final_system_instruction += f"{rag_context}\n"

        # 4. Stream response using Gemini if client is initialized
        if self.client:
            try:
                # Map input messages to Gemini ContentDict format
                gemini_history = []
                for msg in messages[:-1]:
                    role = "model" if msg["role"] == "assistant" or msg["role"] == "model" else "user"
                    gemini_history.append(
                        types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=msg["content"])]
                        )
                    )

                current_prompt = messages[-1]["content"]

                # Create chat with system instruction config
                chat = self.client.chats.create(
                    model="gemini-1.5-flash",
                    history=gemini_history,
                    config=types.GenerateContentConfig(
                        system_instruction=final_system_instruction
                    )
                )

                # Stream response
                response = chat.send_message_stream(current_prompt)
                for chunk in response:
                    if chunk.text:
                        yield chunk.text
                return
            except Exception as e:
                logger.error(f"Error executing Gemini agent stream: {e}. Falling back to Ollama.")
                yield f"\n[Gemini connection failed. Falling back to local Ollama agent...]\n"

        # 5. Try local Ollama execution fallback
        from services.ollama import ollama_service
        if await ollama_service.check_health():
            try:
                yield f"[Local Agent Mode - {active_agent.replace('_', ' ').title()}]\n"
                async for chunk in ollama_service.generate_chat_stream(
                    messages=messages,
                    system_instruction=final_system_instruction
                ):
                    yield chunk
                return
            except Exception as ollama_err:
                logger.error(f"Local Ollama execution failed: {ollama_err}")
                yield f"\n[Local Ollama agent error: {ollama_err}. Falling back to developer mocks...]\n"
        else:
            logger.info("Ollama is not running. Falling back to Developer Mocks.")

        # 6. Final Mock Fallback for local testing/offline validation
        yield f"[Mock Mode - {active_agent.replace('_', ' ').title()}]\n"
        if active_agent == "task_planner":
            yield "I am your Task Planner. I've classified your intent and proposed creating a task for you.\n[ACTIONS]\n[{\"type\": \"add_task\", \"params\": {\"title\": \"Study Session\", \"priority\": \"medium\", \"category\": \"academic\"}}]"
        elif active_agent == "finance_advisor":
            yield "I am your Finance Advisor. I've classified your intent and proposed logging an expense.\n[ACTIONS]\n[{\"type\": \"add_expense\", \"params\": {\"amount\": 15.0, \"category\": \"Food\", \"description\": \"Lunch\"}}]"
        elif active_agent == "health_companion":
            yield "I am your Health Companion. I've classified your intent and proposed updating your health logs.\n[ACTIONS]\n[{\"type\": \"log_health\", \"params\": {\"waterIntakeMl\": 250, \"sleepMinutes\": 480}}]"
        elif active_agent == "academic_coach":
            yield "I am your Academic Coach. I've classified your intent and proposed creating a study milestone task.\n[ACTIONS]\n[{\"type\": \"add_task\", \"params\": {\"title\": \"Review Computer Architecture Notes\", \"priority\": \"high\", \"category\": \"academic\"}}]"
        elif active_agent == "research_agent":
            yield "I am your Research Agent. I've classified your intent and summarized the request. Let me know what facts you'd like me to fetch."
        elif active_agent == "decision_agent":
            yield "I am your Decision Agent. I've classified your intent and am ready to weigh options, compare trade-offs, and perform risk analysis."
        else:
            yield "I am AURA, your personal assistant. How can I assist you with your day today?"

agent_orchestrator = AgentOrchestrator()
