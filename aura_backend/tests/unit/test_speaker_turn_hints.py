"""ADR-057: possible speaker-change points from pauses. Boundaries only: never a speaker label."""

from __future__ import annotations

import pytest

from aura_core.tools.media import speaker_turn_hints


def segs(*spans: tuple[float, float]) -> list[dict]:
    return [{"start": a, "end": b, "text": "x", "speaker": None} for a, b in spans]


def test_a_pause_at_or_above_the_threshold_marks_a_possible_turn_change() -> None:
    data = segs((0.0, 2.0), (2.3, 4.0), (5.5, 7.0), (7.0, 8.0))
    hints = speaker_turn_hints(data)
    assert [s["turn_break_before"] for s in data] == [False, False, True, False]
    assert hints["turn_breaks"] == 1 and hints["estimated_turns"] == 2


def test_the_threshold_is_inclusive_and_configurable() -> None:
    data = segs((0.0, 1.0), (2.2, 3.0))
    assert speaker_turn_hints(data)["turn_breaks"] == 1, "a 1.2 s gap is at the default threshold"
    data = segs((0.0, 1.0), (2.2, 3.0))
    assert speaker_turn_hints(data, pause_s=2.0)["turn_breaks"] == 0


def test_the_first_segment_never_has_a_break_and_silence_gives_no_turns() -> None:
    data = segs((10.0, 11.0))
    assert speaker_turn_hints(data)["estimated_turns"] == 1 and data[0]["turn_break_before"] is False
    assert speaker_turn_hints([]) == {**speaker_turn_hints([]), "turn_breaks": 0, "estimated_turns": 0}


def test_it_says_what_it_is_not_and_leaves_speaker_null() -> None:
    data = segs((0.0, 1.0), (5.0, 6.0))
    hints = speaker_turn_hints(data)
    assert "not identified" in hints["caveat"] and "pause" in hints["method"]
    assert all(s["speaker"] is None for s in data), "a label is never invented"


@pytest.mark.parametrize("overlap", [(0.0, 5.0), (3.0, 8.0)])
def test_overlapping_speech_has_no_gap_and_so_no_break(overlap) -> None:
    data = segs((0.0, 5.0), overlap)
    assert speaker_turn_hints(data)["turn_breaks"] == 0


