/// Time-to-first-byte (headers) timeout for LLM chat streams.
///
/// Matches server `AURA_LLM_TIMEOUT_S` (180s). This is TTFT, not full-stream
/// duration: cold CPU 3B inference can exceed 60s before the first SSE frame
/// while nginx/LLM still allow 180s.
const Duration kLlmConnectTimeout = Duration(seconds: 180);

/// Longest silence tolerated between two chunks of an already-open chat stream.
///
/// The server writes an SSE comment (`: keepalive`) every 15s on `/api/v3/chat/stream`
/// (`SSE_KEEPALIVE_S` in `aura_core/api/v3.py`) while it plans or waits on the model, so a live
/// stream is never silent for longer than that. 45s is three missed keepalives: comfortably past
/// one slow write or a proxy flush, and short enough that a dead connection releases the chat
/// instead of holding it locked indefinitely (audit F-31).
const Duration kChatStreamIdleTimeout = Duration(seconds: 45);
