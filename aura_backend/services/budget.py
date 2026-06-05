import logging
import contextvars

logger = logging.getLogger("aura_backend.services.budget")

# Custom exception for Gemini budget exhaustion
class GeminiBudgetExceededException(Exception):
    def __init__(self, message="AI quota exhausted", retry_after=60):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after

# Request-local context variable to count Gemini API calls
gemini_call_counter = contextvars.ContextVar("gemini_call_counter", default=0)

MAX_GEMINI_CALLS_PER_REQUEST = 2

def increment_and_check_gemini_budget():
    """Increments the Gemini call counter and raises a GeminiBudgetExceededException if it exceeds the request budget."""
    current_calls = gemini_call_counter.get()
    if current_calls >= MAX_GEMINI_CALLS_PER_REQUEST:
        logger.warning(f"Request Gemini budget exceeded: {current_calls} calls. Aborting further calls.")
        raise GeminiBudgetExceededException()
    gemini_call_counter.set(current_calls + 1)
    logger.info(f"Gemini request budget: {current_calls + 1}/{MAX_GEMINI_CALLS_PER_REQUEST}")
