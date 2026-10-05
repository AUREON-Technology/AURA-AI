"""Evidence a calling system hands AURA for one turn (AR-5).

A caller -- Flame in Freefall interpreting an analysis is the first -- often has the facts the
answer must rest on: measured values, condition records, passages it already retrieved. Putting
them in ``message`` would place them on the USER channel, where text is read as instructions
(ADR-006). Here each entry travels like a retrieved chunk instead:

* it is **DATA**: fenced with the turn's nonce, scanned by the same context guards, and dropped
  if quarantined;
* its presence **taints the turn**, so no side-effecting tool can run on its strength without
  explicit confirmation;
* its ``id`` is the citation: an answer grounded in an entry cites that id verbatim, and only
  ids the caller sent (and that reached the context) can ever be cited.

Stdlib only (ADR-001).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError
from .untrusted import MAX_UNTRUSTED_CHARS, UntrustedText

__all__ = ["CallerContext", "MAX_CALLER_CONTEXT", "CALLER_CONTEXT_ID"]

#: Entries per turn. Each may be up to 2000 characters, so this bounds a turn's caller evidence
#: at ~32 KB -- the same order as ``message`` itself.
MAX_CALLER_CONTEXT = 16
#: Safe inside the fence's ``source="..."`` attribute and in a citation: no quotes, spaces,
#: angle brackets or ``@`` (which would read as a document citation, ``doc@vN``).
CALLER_CONTEXT_ID = re.compile(r"^[A-Za-z0-9_\-.:]{1,128}$")
_SOURCE = re.compile(r"^[A-Za-z0-9_\-.:/ ]{0,128}$")


@dataclass(frozen=True, slots=True)
class CallerContext:
    id: str
    text: UntrustedText
    source: str = ""

    @classmethod
    def of(cls, *, id: str, text: str, source: str = "") -> "CallerContext":
        """Validate the id and source, sanitise the text (NFKC, no control or bidi characters)."""
        if not isinstance(id, str) or not CALLER_CONTEXT_ID.match(id):
            raise ValidationError("a context id is 1-128 of A-Z a-z 0-9 _ - . :", details={"id": str(id)[:32]})
        if not isinstance(source, str) or not _SOURCE.match(source):
            raise ValidationError("a context source is up to 128 of A-Z a-z 0-9 _ - . : / and spaces")
        if not isinstance(text, str) or len(text) > MAX_UNTRUSTED_CHARS:
            raise ValidationError(f"context text must be at most {MAX_UNTRUSTED_CHARS} characters")
        clean = UntrustedText.of(text)
        if not clean:
            raise ValidationError("context text is empty after sanitising", details={"id": id})
        return cls(id=id, text=clean, source=source)

    def content(self) -> str:
        """What sits inside the fence: the caller's source label, then the entry's text."""
        return f"{self.source}: {self.text.raw()}" if self.source else self.text.raw()
