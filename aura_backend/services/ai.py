import logging
from typing import AsyncGenerator, List, Dict, Any
import google.generativeai as genai
from config import settings
from services.rag import rag_service

logger = logging.getLogger(__name__)

class AIService:
  def __init__(self):
    if settings.gemini_api_key:
      genai.configure(api_key=settings.gemini_api_key)
      logger.info("Gemini API initialized successfully.")
    else:
      logger.warning("GEMINI_API_KEY is missing. AI calls will fail.")

  async def generate_chat_response_stream(
    self, 
    messages: List[Dict[str, str]], 
    use_rag: bool = True
  ) -> AsyncGenerator[str, None]:
    """Streams responses from Gemini 1.5 Flash, injecting RAG context if enabled."""
    if not settings.gemini_api_key:
      yield "[Backend Alert: GEMINI_API_KEY is not set. Please configure it in your backend .env file.]"
      return

    try:
      # Extract last user message to query the RAG knowledge base
      last_user_message = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), 
        ""
      )

      rag_context = ""
      if use_rag and last_user_message:
        # Search Qdrant
        search_results = rag_service.query_knowledge(last_user_message, limit=3)
        if search_results:
          rag_context = "\n\nRelevant Context from Knowledge Base:\n"
          for idx, res in enumerate(search_results):
            rag_context += f"--- Context Chunk [{idx + 1}] (Score: {res['score']:.2f}):\n{res['content']}\n"
          logger.info("RAG context successfully injected into prompt.")

      # Parse input messages into Gemini's format:
      # List of ContentDict with role ('user' or 'model') and parts
      gemini_history = []
      for msg in messages[:-1]:
        role = "model" if msg["role"] == "assistant" else "user"
        gemini_history.append({
          "role": role,
          "parts": [msg["content"]]
        })

      # The current/last prompt is injected with RAG context
      current_prompt = messages[-1]["content"]
      if rag_context:
        current_prompt = (
          f"Instructions: Use the following retrieved context to help answer the user prompt. "
          f"If the context is irrelevant, you may ignore it.\n"
          f"{rag_context}\n"
          f"User Prompt: {current_prompt}"
        )

      # Create chat model
      model = genai.GenerativeModel("gemini-1.5-flash")
      chat = model.start_chat(history=gemini_history)

      # Stream the content
      response = chat.send_message(current_prompt, stream=True)
      for chunk in response:
        if chunk.text:
          yield chunk.text

    except Exception as e:
      logger.error(f"Error in Gemini streaming generation: {e}")
      yield f"\n[Backend Error: Failed to generate response from Gemini: {e}]"

ai_service = AIService()
