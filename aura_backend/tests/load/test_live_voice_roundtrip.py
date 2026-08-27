"""Opt-in load proof for Phase H live voice (real Piper → Whisper).

Not run in default pytest. Requires real speech engines and is the roadmap exit
gate — not claimed by unit scaffolding alone.

    pytest tests/load/test_live_voice_roundtrip.py -m load -s
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.load


@pytest.mark.load
async def test_live_voice_roundtrip_placeholder() -> None:
    """Stub structure: real Piper WAV → session as mic → Whisper → turn → playback PCM.

    Implement against a deployed stack with ``AURA_VOICE_LIVE_ENABLED=true`` and both
    engines present. Until then this test documents the proof shape and skips.
    """
    pytest.skip(
        "requires real Piper + faster-whisper stack; scaffolding only — see "
        "docs/plans/phase-h-live-voice-chat.md Verification"
    )
