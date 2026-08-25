"""Spoken-text rewrite used by ``EspeakTtsEngine`` before synthesis."""

from __future__ import annotations

from aura_core.adapters.spoken_text import prepare_spoken_text


class TestPrepareSpokenText:
    def test_strips_markdown_and_keeps_the_words(self) -> None:
        raw = "## Status\nThe **invoice** is ready. See [the report](https://example.com/r)."
        # The heading ends in a period: it is a structural boundary, and without one the
        # engine runs "Status" straight into "The invoice" on a single breath.
        assert prepare_spoken_text(raw) == (
            "Status. The invoice is ready. See the report."
        )

    def test_expands_abbreviations_so_they_are_spoken_as_words(self) -> None:
        spoken = prepare_spoken_text("Ship it, e.g. overnight, vs. ground, etc.")
        assert "for example" in spoken
        assert "versus" in spoken
        assert "et cetera" in spoken
        assert "e.g." not in spoken.lower()

    def test_does_not_eat_versus_when_expanding_vs(self) -> None:
        assert "versus" in prepare_spoken_text("This versus that")
        assert "versusus" not in prepare_spoken_text("This versus that")

    def test_drops_urls_and_emails(self) -> None:
        spoken = prepare_spoken_text("Ping me at a@b.com or https://example.com/path")
        assert "http" not in spoken.lower()
        assert "@" not in spoken
        assert "example.com" not in spoken

    def test_url_only_is_unspeakable(self) -> None:
        assert prepare_spoken_text("https://example.com/secret") == ""

    def test_adds_a_final_period_so_espeak_pauses_at_the_end(self) -> None:
        assert prepare_spoken_text("The report is ready").endswith(".")

    def test_blank_is_empty(self) -> None:
        assert prepare_spoken_text("   ") == ""

    def test_arabic_question_mark_is_kept_as_the_sentence_ending(self) -> None:
        spoken = prepare_spoken_text("مرحبا، كيف حالك؟")
        assert spoken == "مرحبا، كيف حالك؟"
        assert spoken.endswith("؟")
        assert not spoken.endswith(".")

    def test_arabic_semicolon_is_a_valid_ending(self) -> None:
        spoken = prepare_spoken_text("الجواب جاهز؛")
        assert spoken.endswith("؛")
        assert not spoken.endswith(".")

    def test_arabic_skips_english_abbreviation_expansions(self) -> None:
        spoken = prepare_spoken_text("النسبة 50% جاهزة؟")
        assert "percent" not in spoken
        assert "50%" in spoken
        assert spoken.endswith("؟")

    def test_keeps_tashkeel(self) -> None:
        spoken = prepare_spoken_text("مَرْحَبًا")
        assert "َ" in spoken
        assert "ْ" in spoken

    def test_english_expansions_still_apply_to_english(self) -> None:
        spoken = prepare_spoken_text("Ship it, e.g. overnight vs. ground.")
        assert "for example" in spoken
        assert "versus" in spoken


class TestPauseStructure:
    """Both engines pause at punctuation only, so structure has to become punctuation."""

    def test_each_bullet_becomes_its_own_sentence(self) -> None:
        raw = "Next steps\n- Send the invoice\n- Archive the thread\n- Log the hours"
        spoken = prepare_spoken_text(raw)
        assert spoken == (
            "Next steps. Send the invoice. Archive the thread. Log the hours."
        )

    def test_numbered_items_are_separated_too(self) -> None:
        spoken = prepare_spoken_text("1. Call the bank\n2. Send the form")
        assert spoken == "Call the bank. Send the form."

    def test_soft_wrapped_paragraph_is_not_chopped_into_sentences(self) -> None:
        # The regression this guards: a terminator per *line* would produce
        # "The invoice is ready and. the total is settled." -- worse than no change at all.
        raw = "The invoice is ready and\nthe total is settled."
        assert prepare_spoken_text(raw) == "The invoice is ready and the total is settled."

    def test_wrapped_list_item_stays_one_sentence(self) -> None:
        raw = "- Send the invoice and\n  archive the thread\n- Log the hours"
        assert prepare_spoken_text(raw) == (
            "Send the invoice and archive the thread. Log the hours."
        )

    def test_paragraph_break_ends_a_sentence(self) -> None:
        spoken = prepare_spoken_text("The invoice is ready\n\nNothing else is due")
        assert spoken == "The invoice is ready. Nothing else is due."

    def test_trailing_comma_is_replaced_not_doubled(self) -> None:
        spoken = prepare_spoken_text("- Send it,\n- Archive it")
        assert ",." not in spoken
        assert spoken == "Send it. Archive it."

    def test_item_that_already_ends_in_punctuation_is_left_alone(self) -> None:
        spoken = prepare_spoken_text("- Is it ready?\n- Yes!")
        assert spoken == "Is it ready? Yes!"

    def test_arabic_bullets_get_pause_points_as_well(self) -> None:
        spoken = prepare_spoken_text("- الفاتورة جاهزة\n- لا شيء آخر")
        assert spoken.count(".") == 2


class TestNumberVerbalization:
    """Only the two constructs espeak reads wrongly are rewritten."""

    def test_currency_is_reordered_and_cents_are_named(self) -> None:
        # Espeak reads "$1,250.50" as "dollar one thousand two hundred fifty point five zero".
        spoken = prepare_spoken_text("The total is $1,250.50 today")
        assert "1250 dollars and 50 cents" in spoken
        assert "$" not in spoken

    def test_whole_amount_has_no_cents_clause(self) -> None:
        assert "42 dollars" in prepare_spoken_text("It cost $42")
        assert "cent" not in prepare_spoken_text("It cost $42")

    def test_singular_amounts_are_singular(self) -> None:
        spoken = prepare_spoken_text("Just $1.01 left")
        assert "1 dollar and 1 cent" in spoken
        assert "dollars" not in spoken

    def test_single_decimal_digit_is_read_as_tens(self) -> None:
        # "$3.5" is three dollars fifty, not three dollars and five.
        assert "3 dollars and 50 cents" in prepare_spoken_text("Owe $3.5 still")

    def test_pounds_use_pence_not_pennies(self) -> None:
        assert "pence" in prepare_spoken_text("It was £2.30 exactly")

    def test_zero_cents_are_not_spoken(self) -> None:
        spoken = prepare_spoken_text("Exactly $80.00 due")
        assert "80 dollars" in spoken
        assert "cent" not in spoken

    def test_iso_date_becomes_a_spoken_date(self) -> None:
        spoken = prepare_spoken_text("Due on 2026-08-25 at the latest")
        assert "August 25th, 2026" in spoken
        assert "-" not in spoken

    def test_ordinal_suffix_handles_the_teens(self) -> None:
        assert "November 11th" in prepare_spoken_text("On 2026-11-11 we ship")
        assert "March 3rd" in prepare_spoken_text("On 2026-03-03 we ship")
        assert "May 1st" in prepare_spoken_text("On 2026-05-01 we ship")

    def test_impossible_date_is_left_alone(self) -> None:
        assert "2026-19-99" in prepare_spoken_text("Ref 2026-19-99 is the ticket")

    def test_times_and_percentages_are_left_to_the_engine(self) -> None:
        # Espeak already says "three thirty PM" and "fifteen percent"; rewriting would risk
        # making them worse, so these must pass through untouched.
        spoken = prepare_spoken_text("At 3:30 PM we hit 15% capacity")
        assert "3:30" in spoken
        assert "15" in spoken

    def test_arabic_replies_skip_currency_rewriting(self) -> None:
        spoken = prepare_spoken_text("المبلغ $50 فقط")
        assert "dollars" not in spoken
        assert "$50" in spoken
