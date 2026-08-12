import logging
from typing import AsyncGenerator, List, Dict, Any
from config import settings
from services.ollama import ollama_service
from services.rag import rag_service

logger = logging.getLogger(__name__)

class AIService:
  """Chat generation, running on our own inference host.

  There is no client to construct and no key to warn about: `ollama_service` is a module singleton
  pointing at settings.ollama_host. Nothing here reaches a third party, so the class has no
  meaningful constructor state left.
  """

  async def generate_chat_response_stream(
    self, 
    messages: List[Dict[str, str]], 
    use_rag: bool = True,
    user_id: str = None
  ) -> AsyncGenerator[str, None]:
    """Streams a response from the local model, injecting RAG context first."""
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

    # Concurrency guard. Formerly a spend cap on a metered API; now it bounds how many
    # simultaneous generations we ask of a single local model, which is the scarcest resource on
    # the box. Without it, background analysis jobs starve interactive chat.
    from services.budget import increment_and_check_gemini_budget, GeminiBudgetExceededException
    try:
      increment_and_check_gemini_budget()
    except GeminiBudgetExceededException as budget_exc:
      yield f"\n[Error: {budget_exc.message}. Please retry after {budget_exc.retry_after} seconds.]\n"
      return

    try:
      async for chunk in ollama_service.generate_chat_stream(modified_messages):
        yield chunk
    except Exception as e:
      # No second engine to fall back to, by design. Reporting the failure is the correct
      # behaviour -- the alternative was a hosted API, and reaching for one is what this change
      # removes. Silence or a fabricated answer would be worse than an honest error.
      logger.error(f"Local inference failed during streaming generation: {e}")
      yield f"\n[Local inference unavailable: {e}]"

ai_service = AIService()
