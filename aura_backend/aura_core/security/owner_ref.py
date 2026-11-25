"""A keyed, one-way reference to a turn's owner, for per-turn records that carry no identity.

``aura_turn_telemetry`` is content-free on purpose: no prompt, no answer, no user id. That left
no way to let a person read the usage of *their own* turn without letting anyone who learns a
``turn_id`` read anyone's. ``owner_ref`` is the narrowest thing that closes that gap: the HMAC of
the user id under a key derived from the deployment's secret. The row can answer "is this caller
the owner?" and nothing else -- the reference cannot be turned back into a user id, and without
the secret it cannot even be recomputed to confirm a guess.

It is personal data all the same (it singles out one person's turns), so it is handled as such:
purged with the account (``erase_account``) and with the telemetry retention window, and never
written to a log, a metric label or ``TurnTelemetry.as_dict``.

Fails closed: without a secret there is no reference (``None``), a row recorded without one can
never be read back, and a lookup without one finds nothing. Rotating the secret orphans existing
rows, which become unreadable rather than readable by someone else.

stdlib only: the domain and engines may import it (``tests/architecture``).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable

__all__ = ["OwnerRefMaker", "owner_ref_maker"]

#: Domain separation: the same deployment secret signs host-agent frames and tokens; a key derived
#: under this label cannot be used for, or replayed as, any of them.
_KEY_LABEL = b"aura:turn-telemetry-owner-ref:v1"
_REF_HEX_CHARS = 32  # 128 bits: ample against collision across one deployment's users

OwnerRefMaker = Callable[[str], "str | None"]


def owner_ref_maker(secret: Callable[[], str]) -> OwnerRefMaker:
    """A function from a user id to its reference, deriving the key from ``secret()`` on each call.

    ``secret`` is a callable because the JWT secret is hydrated from a secret file *after* the
    settings load (see ``InfrastructureSettings.host_agent_relay_secret``): reading it at call
    time is what makes the key the one every gateway worker shares. Deriving is one HMAC; there
    is nothing worth caching.
    """

    def make(user_id: str) -> str | None:
        raw = secret()
        if not raw or not user_id:
            return None
        key = hmac.new(raw.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()
        return hmac.new(key, user_id.encode("utf-8"), hashlib.sha256).hexdigest()[:_REF_HEX_CHARS]

    return make
