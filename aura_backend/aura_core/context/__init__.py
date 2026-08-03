"""Context engine: token budgeting, ranking, compression, and fenced assembly.

The stage that decides what the model sees. Untrusted material is rendered as fenced data with
provenance, never concatenated into the instruction channel (ADR-006).
"""

from .engine import AssembledContext, ContextEngine, ContextRequest
from .tokenizer import HeuristicTokenizer, TiktokenTokenizer, Tokenizer, get_tokenizer

__all__ = [
    "ContextEngine",
    "ContextRequest",
    "AssembledContext",
    "Tokenizer",
    "HeuristicTokenizer",
    "TiktokenTokenizer",
    "get_tokenizer",
]
