import logging
import contextvars

logger = logging.getLogger("aura_backend.services.budget")

# This module was named for a hosted vendor, back when a request's inference budget existed to
# bound spend on a metered external API. Inference is now local, so the reason changed but the
# need did not: the cap now bounds *latency and contention*. A 7B model on shared CPU serves
# roughly one generation at a time, so a request that fans out to four of them does not cost four
# times as much money — it makes three other users wait.
#
# All four importing modules were renamed in the same commit, so no compatibility aliases are
# kept here. Aliases would have been the cautious choice for a published library; for four call
# sites in one repository they are just a second name for the same thing, and a second name is
# how the old vocabulary survives a rename.


class InferenceBudgetExceededException(Exception):
    """Raised when one request has asked for more generations than it is allowed."""

    def __init__(self, message="AI quota exhausted", retry_after=60):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


# Request-local, so the count resets per request without any explicit teardown. A module-level
# int would leak across requests in a long-lived worker and eventually refuse everything.
inference_call_counter = contextvars.ContextVar("inference_call_counter", default=0)

MAX_INFERENCE_CALLS_PER_REQUEST = 2


def increment_and_check_inference_budget():
    """Count one generation against this request's budget, raising once it is spent."""
    current_calls = inference_call_counter.get()
    if current_calls >= MAX_INFERENCE_CALLS_PER_REQUEST:
        logger.warning(
            f"Request inference budget exceeded: {current_calls} calls. Aborting further calls."
        )
        raise InferenceBudgetExceededException()
    inference_call_counter.set(current_calls + 1)
    logger.info(f"Request inference budget: {current_calls + 1}/{MAX_INFERENCE_CALLS_PER_REQUEST}")
