"""The product's supported languages and how a client-reported language tag maps onto them.

Shared by the v3 gateway (``X-Client-Language`` on a chat turn) and the v2 platform adapters that
store it on the user (``services/user_language.py``), so both accept exactly the same tags.
"""

from __future__ import annotations

import re

__all__ = ["CLIENT_LANGUAGE_HEADER", "DEFAULT_LANGUAGE", "SUPPORTED_LANGUAGES", "normalize_language"]

#: The header the app sends its display language in.
CLIENT_LANGUAGE_HEADER = "X-Client-Language"

#: Languages the product ships. A BCP-47 tag is reduced to its primary subtag ("ar-EG" -> "ar").
SUPPORTED_LANGUAGES = frozenset({"ar", "en"})

DEFAULT_LANGUAGE = "en"

#: A BCP-47-shaped tag (or a POSIX one with "_"): short enough for the VARCHAR(16) column and
#: tight enough that the header can carry nothing but a language tag.
_TAG = re.compile(r"^([A-Za-z]{2,3})(?:[-_][A-Za-z0-9]{1,8}){0,3}$")


def normalize_language(value: object) -> str | None:
    """``"ar"`` or ``"en"`` for a tag naming one of them, ``None`` for anything else.

    Accepts ``ar``, ``ar-EG``, ``ar_EG``, ``en-US`` and so on, case-insensitively. ``None`` lets a
    caller treat a missing, malformed or unsupported value as "keep what we have".
    """
    if not isinstance(value, str) or len(value) > 35:
        return None
    match = _TAG.match(value.strip())
    if match is None:
        return None
    primary = match.group(1).lower()
    return primary if primary in SUPPORTED_LANGUAGES else None
