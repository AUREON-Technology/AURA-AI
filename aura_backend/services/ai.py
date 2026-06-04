import logging
from typing import AsyncGenerator, List, Dict, Any
from google import genai
from google.genai import types
from config import settings
from services.rag import rag_service

logger = logging.getLogger(__name__)

class AIService:
  def __init__(self):
    self.client = None
    if settings.gemini_api_key:
      self.client = genai.Client(api_key=settings.gemini_api_key)
      logger.info("Gemini API client initialized successfully.")
    else:
      logger.warning("GEMINI_API_KEY is missing. AI calls will fail.")

  async def generate_chat_response_stream(
    self, 
    messages: List[Dict[str, str]], 
    use_rag: bool = True,
    user_id: str = None
  ) -> AsyncGenerator[str, None]:
    """Streams responses from Gemini 1.5 Flash, injecting RAG context, and falls back to Ollama if offline."""
    # Extract last user message to query the RAG knowledge base
    last_user_message = next(
      (m["content"] for m in reversed(messages) if m["role"] == "user"), 
      ""
    )

    rag_context = ""
    if use_rag and last_user_message:
      # Search Qdrant
      search_results = rag_service.query_knowledge(last_user_message, limit=3, user_id=user_id)
      if search_results:
        rag_context = "\n\nRelevant Context from Knowledge Base:\n"
        for idx, res in enumerate(search_results):
          rag_context += f"--- Context Chunk [{idx + 1}] (Score: {res['score']:.2f}):\n{res['content']}\n"
        logger.info("RAG context successfully injected into prompt.")

    # Create a copy of the messages and modify the last user message with RAG context
    modified_messages = [dict(m) for m in messages]
    if rag_context and modified_messages:
      # Find the last user message and inject context
      for i in range(len(modified_messages) - 1, -1, -1):
        if modified_messages[i]["role"] == "user":
          modified_messages[i]["content"] = (
            f"Instructions: Use the following retrieved context to help answer the user prompt. "
            f"If the context is irrelevant, you may ignore it.\n"
            f"{rag_context}\n"
            f"User Prompt: {modified_messages[i]['content']}"
          )
          break

    # If Gemini API key is missing, fallback immediately to Ollama
    if not self.client:
      logger.info("Gemini client is not initialized. Falling back to local Ollama.")
      yield "[Running in Local Offline Mode - Powered by Ollama]\n"
      try:
        from services.ollama import ollama_service
        async for chunk in ollama_service.generate_chat_stream(modified_messages):
          yield chunk
      except Exception as fallback_error:
        yield f"\n[Offline Fallback Error: Failed to run Ollama: {fallback_error}]"
      return

    try:
      # Parse input messages into Gemini's format:
      gemini_history = []
      for msg in modified_messages[:-1]:
        role = "model" if msg["role"] == "assistant" or msg["role"] == "model" else "user"
        gemini_history.append(
          types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])]
          )
        )

      current_prompt = modified_messages[-1]["content"]

      # Create chat model and stream
      chat = self.client.chats.create(model="gemini-2.5-flash", history=gemini_history)
      response = chat.send_message_stream(current_prompt)
      for chunk in response:
        if chunk.text:
          yield chunk.text

    except Exception as e:
      logger.error(f"Error in Gemini streaming generation: {e}. Falling back to Ollama.")
      yield f"\n[Gemini connection failed. Falling back to local Ollama assistant...]\n"
      try:
        from services.ollama import ollama_service
        async for chunk in ollama_service.generate_chat_stream(modified_messages):
          yield chunk
      except Exception as fallback_error:
        yield f"\n[Offline Fallback Error: Failed to run Ollama: {fallback_error}]"

ai_service = AIService()
