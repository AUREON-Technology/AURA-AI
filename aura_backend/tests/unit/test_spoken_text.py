"""Spoken-text rewrite used by ``EspeakTtsEngine`` before synthesis."""

from __future__ import annotations

from aura_core.adapters.spoken_text import prepare_spoken_text


class TestPrepareSpokenText:
    def test_strips_markdown_and_keeps_the_words(self) -> None:
        raw = "## Status\nThe **invoice** is ready. See [the report](https://example.com/r)."
        assert prepare_spoken_text(raw) == (
            "Status The invoice is ready. See the report."
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
