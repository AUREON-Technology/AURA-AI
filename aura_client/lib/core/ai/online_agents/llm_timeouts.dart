/// Time-to-first-byte (headers) timeout for LLM chat streams.
///
/// Matches server `AURA_LLM_TIMEOUT_S` (180s). This is TTFT, not full-stream
/// duration: cold CPU 3B inference can exceed 60s before the first SSE frame
/// while nginx/LLM still allow 180s.
const Duration kLlmConnectTimeout = Duration(seconds: 180);
