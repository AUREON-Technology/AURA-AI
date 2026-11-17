"""Language-aware text handling shared by retrieval, safety, routing and voice. Stdlib only."""

from .arabic import (
    ARABIC_STOPWORDS,
    arabic_view,
    dominant_script,
    fold,
    has_arabic,
    is_arabic_token,
    light_stem,
)

__all__ = [
    "ARABIC_STOPWORDS",
    "arabic_view",
    "dominant_script",
    "fold",
    "has_arabic",
    "is_arabic_token",
    "light_stem",
]
