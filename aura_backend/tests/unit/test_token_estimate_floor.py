"""The token estimate cannot be driven below ``len/4`` (security review of KAN-103/104).

The first version of ``domain/tokens.py`` matched Arabic letters only in some sub-ranges of the
Arabic blocks, while the "other scripts" rule excluded the whole blocks. Letters in the gap --
U+06D5, U+06EE, most presentation forms, much of Extended-A -- matched neither rule and counted
as nothing, so a prompt padded with them under-reserved ``USER_TPD``, the provider TPM and the
context cap. Two independent fixes are pinned here:

1. the two rules partition the Arabic blocks exactly, so every letter there is costed per
   letter (checked *above* the floor, so the floor cannot mask a regression);
2. a floor: no input counts below the flat ``ceil(len/4)`` every caller used before.
"""

from __future__ import annotations

import math
import random

import pytest

from aura_core.domain.messages import Message
from aura_core.domain.tokens import GENERIC, PROFILES, estimate_tokens

#: The Arabic blocks the estimator's two letter rules split between them.
ARABIC_BLOCKS = {
    "arabic": (0x0600, 0x06FF),
    "arabic_supplement": (0x0750, 0x077F),
    "arabic_extended_a_b": (0x08A0, 0x08FF),
    "presentation_forms_a": (0xFB50, 0xFDFF),
    "presentation_forms_b": (0xFE70, 0xFEFF),
}

#: Ranges the first version left uncovered (letters in them counted as ~0).
PREVIOUSLY_UNCOVERED = {
    "arabic_tail": (0x06D4, 0x06F9),
    "extended_a_tail": (0x08CA, 0x08FF),
    "presentation_a_tail": (0xFDFC, 0xFDFF),
    "presentation_b_tail": (0xFEFD, 0xFEFF),
}


def _floor(text: str) -> int:
    return math.ceil(len(text) / 4)


def _chars(lo: int, hi: int) -> list[str]:
    return [chr(cp) for cp in range(lo, hi + 1)]


def _letters(lo: int, hi: int) -> list[str]:
    return [c for c in _chars(lo, hi) if c.isalnum() and not c.isdigit()]


@pytest.mark.parametrize("block", sorted(ARABIC_BLOCKS))
def test_every_arabic_block_letter_is_costed_per_letter(block: str) -> None:
    """Above the floor: 40 copies of any letter cost what 40 Arabic letters cost (~21), not the
    floor's 10. This is what fails if the two rules stop partitioning the blocks."""
    per_letter = 40 / GENERIC.arabic_chars_per_token * GENERIC.safety
    for char in _letters(*ARABIC_BLOCKS[block]):
        assert estimate_tokens(char * 40) >= int(per_letter), (block, hex(ord(char)))


@pytest.mark.parametrize("block", sorted(PREVIOUSLY_UNCOVERED))
@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_strings_of_previously_uncovered_characters_count_at_least_len_over_four(
    block: str, profile: str
) -> None:
    chars = _chars(*PREVIOUSLY_UNCOVERED[block])
    padding = "".join(chars) * 20
    assert estimate_tokens(padding, PROFILES[profile]) >= _floor(padding)
    letters = "".join(_letters(*PREVIOUSLY_UNCOVERED[block]))
    if letters:
        padded = letters * 40
        assert estimate_tokens(padded, PROFILES[profile]) > _floor(padded), block


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_fuzz_over_every_arabic_block_never_falls_below_len_over_four(profile: str) -> None:
    """Every code point of every Arabic block, alone and repeated, plus seeded random mixes of
    them with spaces and Latin text."""
    tokens_profile = PROFILES[profile]
    population = [c for lo, hi in ARABIC_BLOCKS.values() for c in _chars(lo, hi)]
    for char in population:
        for text in (char, char * 7, char * 64, f"{char} " * 16):
            assert estimate_tokens(text, tokens_profile) >= _floor(text), (profile, hex(ord(char)))
    rng = random.Random(20261121)
    for _ in range(400):
        pieces = rng.choices(population + [" ", "a", "\n"], k=rng.randint(1, 300))
        text = "".join(pieces)
        assert estimate_tokens(text, tokens_profile) >= _floor(text), (profile, text[:20])


def test_no_non_space_bmp_character_is_free() -> None:
    """Any character outside whitespace, repeated, is seen by some rule (it counts above the
    floor), so no script or symbol class is an unpriced padding vector."""
    unseen = []
    for cp in range(0x21, 0x10000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        char = chr(cp)
        if char.isspace():
            continue
        if estimate_tokens(char * 40) <= _floor(char * 40):
            unseen.append(hex(cp))
    assert not unseen, unseen[:20]


def test_whitespace_padding_is_held_at_the_floor() -> None:
    padding = " " * 4000 + "\t" * 4000
    assert estimate_tokens(padding) >= _floor(padding) == 2000


def test_the_message_estimate_inherits_the_floor() -> None:
    text = "ە" * 1000
    assert Message.user(text).approx_tokens() >= _floor(text) + 4
