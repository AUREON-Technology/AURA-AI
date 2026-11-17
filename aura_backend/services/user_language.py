"""A user's language: what the app reports, what is stored, and what readers fall back to.

The app sends the language it is displaying in ``X-Client-Language`` (its in-app choice, or the
device language when the user left it on "System"). A request that carries it answers in it
directly; ``users.language`` (migration 0021) keeps it for work with no request to read it from,
such as the daily briefing. Anything missing or unrecognised is ignored rather than stored, so an
old app, or a header with junk in it, never resets a user's saved choice; and a user who never
reported one reads as English, which is what every user got before this module existed
(docs/plans/arabic-language_plan.md, stage 1). Which tags are accepted is
``aura_core/text/language.py``'s decision, shared with the v3 gateway.
"""

from __future__ import annotations

from aura_core.text.language import (
    CLIENT_LANGUAGE_HEADER as LANGUAGE_HEADER,
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    normalize_language,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGE_HEADER",
    "SUPPORTED_LANGUAGES",
    "normalize_language",
    "remember_language",
    "resolve_language",
]


def remember_language(user: object, reported: object) -> bool:
    """Store the language the app reported on ``user`` (anything with a ``language`` attribute).

    Returns whether the stored value changed. An absent or unrecognised value changes nothing.
    """
    valid = normalize_language(reported)
    if valid is None or getattr(user, "language", None) == valid:
        return False
    user.language = valid  # type: ignore[attr-defined]
    return True


def resolve_language(stored: object) -> str:
    """The language to write server-side text in: the stored one if valid, else English."""
    return normalize_language(stored) or DEFAULT_LANGUAGE
