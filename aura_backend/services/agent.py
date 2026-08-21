import json
import logging
import asyncio
from typing import List, Dict, Any, AsyncGenerator
from config import settings
from services.memory import memory_service
from services.ollama import ollama_service
from services.rag import rag_service
from services.budget import increment_and_check_inference_budget, InferenceBudgetExceededException


logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = {
    "orchestrator": "You are AURA (Autonomous Utility & Retrieval Assistant), a premium Personal AI Operating System and Executive Assistant. Your tone is professional, intelligent, and refined. When in orchestrator mode, help the user with general queries, and delegate specialized tasks to your specialized personas. Keep responses concise.",
    "task_planner": "You are AURA's Task Planner. Help the user prioritize tasks, outline subtask checklists, and manage schedules. Suggest tasks whenever appropriate.",
    "finance_advisor": "You are AURA's Finance Advisor. Analyze expenses, suggest budgets, and provide smart financial recommendations. Focus on helping the user stay within their means.",
    "health_companion": "You are AURA's Health Companion. Help track water, sleep, calories, weight, and exercise. Offer encouraging, evidence-based wellness tips.",
    "academic_coach": "You are AURA's Academic Coach. Assist with software engineering specifications, coding structures, learning topics, study guides, and flashcards.",
    "research_agent": "You are AURA's Research Agent. Help the user gather facts, summarize articles or documents, analyze web contents, and perform structured knowledge synthesis. Focus on objective, logical, and well-cited information retrieval. If the user asks to find, search, or extract information from a file in their Google Drive (such as a PDF), you MUST autonomously use the tools: first call search_google_drive to find matching files, then call ingest_google_drive_pdf to ingest the file, and then call query_knowledge_base to search for the requested information. Do NOT ask the user for confirmation or permission before calling these tools; execute them immediately in sequence.",
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
        # Inference is `ollama_service`, a module singleton. No client and no key to hold.
        pass

    def classify_intent(self, user_message: str) -> str:
        """Classifies user intent to route message to the appropriate specialized agent using semantic router, falling back to keywords."""
        msg_lower = user_message.lower()
        try:
            from services.agent_router import agent_router
            intent = agent_router.route_intent(user_message)
            logger.info(f"Semantic Agent Router routed intent: {intent}")
            
            intent_mapping = {
                "chat_agent": "orchestrator",
                "memory_agent": "orchestrator",
                "rag_agent": "research_agent",
                "automation_agent": "task_planner",
                "knowledge_agent": "research_agent",
                "coding_agent": "academic_coach",
                "planning_agent": "decision_agent" if any(k in msg_lower for k in ["decide", "decision", "choose", "compare", "risk", "trade-off", "cost-benefit", "eval", "evaluate", "pros and cons"]) else "task_planner"
            }
            mapped_agent = intent_mapping.get(intent, "orchestrator")
            if mapped_agent != "orchestrator":
                return mapped_agent
        except Exception as router_err:
            logger.warning(f"Semantic routing failed, falling back to keywords: {router_err}")
        
        # 1. Task Planner
        if any(k in msg_lower for k in ["task", "todo", "to-do", "schedule", "remind", "reminder", "checklist", "deadline", "calendar", "event", "appointment"]):
            return "task_planner"
            
        # 2. Finance Advisor
        if any(k in msg_lower for k in ["expense", "income", "money", "spent", "budget", "cost", "price", "dollars", "usd", "transaction", "financial", "finance"]):
            return "finance_advisor"
            
        # 3. Health Companion
        if any(k in msg_lower for k in ["water", "sleep", "calorie", "workout", "weight", "health", "fitness", "calories", "exercise", "run", "gym"]):
            return "health_companion"
            
        # 4. Academic Coach
        if any(k in msg_lower for k in ["study", "course", "exam", "code", "lecture", "university", "assignment", "class", "learn", "homework", "programming", "develop"]):
            return "academic_coach"
            
        # 5. Research Agent
        if any(k in msg_lower for k in ["search", "google", "summarize", "find facts", "research", "drive", "pdf", "file", "document", "extract"]):
            return "research_agent"
            
        # 6. Decision Agent
        if any(k in msg_lower for k in ["decide", "decision", "choose", "compare", "risk", "trade-off", "cost-benefit", "eval", "evaluate", "pros and cons"]):
            return "decision_agent"
            
        return "orchestrator"

    async def execute_agent_stream(
        self,
        agent_id: str,
        messages: List[Dict[str, str]],
        custom_instructions: Dict[str, str] = None,
        use_rag: bool = True,
        user_id: str = None,
        google_access_token: str = None
    ) -> AsyncGenerator[str, None]:
        """Streams response for selected agent. If Gemini is offline, falls back to Ollama; if Ollama is also offline, uses mocks."""
        # Define inline tools to capture google_access_token and user_id via closure
        def search_google_drive(query: str) -> dict:
            """Search for PDF files in the user's Google Drive by file name.
            
            Args:
                query: A search string/keyword to filter file names.
            """
            if not google_access_token:
                return {"error": "Google Drive access token is missing. Please log in with Google."}
            from services.drive import search_drive_pdfs
            files = search_drive_pdfs(google_access_token, query)
            return {"files": files}

        def ingest_google_drive_pdf(file_id: str, file_name: str) -> dict:
            """Download a PDF file from Google Drive and ingest its text content into AURA's Knowledge Base (RAG) in the background.
            
            Args:
                file_id: The unique Google Drive file ID.
                file_name: The display name of the file.
            """
            if not google_access_token:
                return {"error": "Google Drive access token is missing. Please log in with Google."}
            from services.drive import queue_drive_pdf_ingest
            return queue_drive_pdf_ingest(file_id, file_name, google_access_token, user_id)

        def query_knowledge_base(query: str) -> dict:
            """Query AURA's local Knowledge Base (RAG) for matching text passages or facts.
            
            Args:
                query: Search terms, IDs, or question keywords.
            """
            from services.rag import rag_service
            results = rag_service.query_knowledge(query, limit=5, user_id=user_id)
            return {"results": results}
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

        # Sentiment Analysis & Behavior Adaptation
        if last_user_message:
            try:
                from services.sentiment import sentiment_analyzer
                sentiment = sentiment_analyzer.analyze_sentiment(last_user_message)
                sentiment_instructions = ""
                if sentiment == "Frustrated":
                    sentiment_instructions = "\n[SYSTEM ADAPTATION: Empathic Tone Required. The user is frustrated. Respond apologetically, show understanding, remain extremely calm and helpful, and address the issue directly.]\n"
                elif sentiment == "Urgent":
                    sentiment_instructions = "\n[SYSTEM ADAPTATION: Urgent Request. The user requires immediate answers. Skip conversational fluff and provide highly concise, direct, actionable steps.]\n"
                elif sentiment == "Negative":
                    sentiment_instructions = "\n[SYSTEM ADAPTATION: Professional Demeanor. The user tone is negative. Respond with high professionalism, address concerns neutrally, and offer clear facts.]\n"
                elif sentiment == "Positive":
                    sentiment_instructions = "\n[SYSTEM ADAPTATION: Encouraging Tone. The user is positive. Be warm, friendly, and match their constructive energy.]\n"
                
                if sentiment_instructions:
                    system_instruction += sentiment_instructions
            except Exception as e:
                logger.error(f"Sentiment evaluation failed: {e}")

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

        # 4. Run the agent turn on local inference, with tool calling.
        #
        # Tools are advertised only when a Drive token is present, matching the previous behaviour:
        # two of the three reach Google Drive on the user's behalf and are useless without it, and
        # advertising an unusable tool invites the model to call it and then apologise.
        tool_specs = []
        tool_impls = {}
        if google_access_token:
            tool_impls = {
                "search_google_drive": search_google_drive,
                "ingest_google_drive_pdf": ingest_google_drive_pdf,
                "query_knowledge_base": query_knowledge_base,
            }
            tool_specs = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_google_drive",
                        "description": "Search the user's Google Drive for files matching a query.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Search terms."}
                            },
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "ingest_google_drive_pdf",
                        "description": (
                            "Download a PDF from the user's Google Drive and add it to the "
                            "knowledge base so it can be searched."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_id": {"type": "string", "description": "Drive file id."},
                                "file_name": {"type": "string", "description": "File name."},
                            },
                            "required": ["file_id", "file_name"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "query_knowledge_base",
                        "description": "Search the already-ingested knowledge base.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "What to look for."}
                            },
                            "required": ["query"],
                        },
                    },
                },
            ]

        try:
            increment_and_check_inference_budget()
        except InferenceBudgetExceededException as budget_exc:
            yield f"\n[Error: {budget_exc.message}. Please retry after {budget_exc.retry_after} seconds.]\n"
            return

        try:
            async for chunk in ollama_service.generate_agent_stream(
                messages=messages,
                system_instruction=final_system_instruction,
                tools=tool_specs,
                tool_impls=tool_impls,
            ):
                yield chunk
            return
        except Exception as e:
            # Only reached when local inference failed *before* emitting anything -- the generator
            # raises in that case precisely so this branch keeps a free choice.
            logger.error(f"Local agent execution failed: {e}")
            if not settings.debug_mode:
                # Production ends here. There is no second engine, by design: that was the hosted
                # API, and removing it is the point of this change. The developer mocks below must
                # never serve a real user -- returning invented task and expense actions because
                # inference was unreachable is fabricating an answer, and the caller would have no
                # way to tell.
                import json
                yield json.dumps({
                    "success": False,
                    "error": "AURA AI is currently unavailable: local inference did not respond.",
                    "retry_after": 60
                })
                return
            # Debug only: fall through to the mock responses below, which exist so the agent
            # routing can be exercised offline with no model running.
            yield f"\n[Local inference unavailable: {e}. Falling back to developer mocks...]\n"

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
