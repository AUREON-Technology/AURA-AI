"""Context engine: token budgeting, ranking, compression, and fenced assembly.

The stage that decides what the model sees. Untrusted material is rendered as fenced data with
provenance, never concatenated into the instruction channel (ADR-006).
"""

from .engine import (
    DEFAULT_HISTORY_TURNS,
    DEFAULT_KNOWLEDGE_CHUNK_MAX_CHARS,
    DEFAULT_MAX_KNOWLEDGE_ITEMS,
    DEFAULT_MAX_PROMPT_TOKENS,
    AssembledContext,
    ContextEngine,
    ContextRequest,
)
from .tokenizer import HeuristicTokenizer, TiktokenTokenizer, Tokenizer, get_tokenizer

__all__ = [
    "ContextEngine",
    "ContextRequest",
    "AssembledContext",
    "DEFAULT_MAX_PROMPT_TOKENS",
    "DEFAULT_HISTORY_TURNS",
    "DEFAULT_KNOWLEDGE_CHUNK_MAX_CHARS",
    "DEFAULT_MAX_KNOWLEDGE_ITEMS",
    "Tokenizer",
    "HeuristicTokenizer",
    "TiktokenTokenizer",
    "get_tokenizer",
]
