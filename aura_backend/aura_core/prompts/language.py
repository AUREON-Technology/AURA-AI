"""Which language a reply should be written in (owner decisions 3 and 5,
docs/plans/arabic-language_plan.md).

The reply mirrors the language the user wrote in. Only when the message has no clear language --
nothing but a link, a number, a code snippet -- does the app's display language decide. The
detection is :func:`aura_core.text.dominant_script`, the same rule that picks the TTS voice, so a
reply is never written in one language and spoken in another.
"""

from __future__ import annotations

import re

from ..text import dominant_script

__all__ = ["SUPPORTED", "reply_language"]

#: Languages a reply can be steered to.
SUPPORTED = frozenset({"ar", "en"})

#: Spans that say nothing about the language the user writes in: fenced and inline code, and
#: URLs, whose Latin letters would otherwise make every pasted link "English".
_NOT_PROSE = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+|www\.\S+", re.DOTALL)


def reply_language(message: str, preferred: str | None = None) -> str | None:
    """``"ar"``/``"en"`` for the reply to ``message``, or ``None`` when nothing decides it.

    ``preferred`` is the app's display language; anything other than a supported code is
    ignored.
    """
    detected = dominant_script(_NOT_PROSE.sub(" ", message or ""))
    if detected is not None:
        return detected
    return preferred if preferred in SUPPORTED else None

