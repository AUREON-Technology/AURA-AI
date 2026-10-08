"""``RepositoryIdentity``: the single ``owner/name`` parser shared by ``tools/coding.py``'s
tool bodies and the pre-dispatch schema check (``tools/schema.py``, ``format="github_repo"``).

The `parse_or_none` cases here are the ones that matter for the engineer-orchestration
hardening: every shape a user actually pastes must still parse (no regression versus the old
``_normalize_repo``), and a bare, ownerless word -- the exact shape an LLM invents when it
fabricates a repository identifier for what is really a local file -- must not.
"""

from __future__ import annotations

import pytest

from aura_core.domain.repository_identity import RepositoryIdentity


class TestParseOrNone:
    @pytest.mark.parametrize(
        "given,owner,name",
        [
            ("acme/widgets", "acme", "widgets"),
            ("https://github.com/acme/widgets", "acme", "widgets"),
            ("https://github.com/acme/widgets.git", "acme", "widgets"),
            ("http://www.github.com/acme/widgets/", "acme", "widgets"),
            ("git@github.com:acme/widgets.git", "acme", "widgets"),
            ("https://github.com/acme/widgets/tree/main/src", "acme", "widgets"),
        ],
    )
    def test_every_pasted_form_parses(self, given: str, owner: str, name: str) -> None:
        identity = RepositoryIdentity.parse_or_none(given)
        assert identity is not None
        assert identity.owner == owner
        assert identity.name == name
        assert identity.slug == f"{owner}/{name}"
        assert str(identity) == f"{owner}/{name}"

    @pytest.mark.parametrize(
        "given",
        [
            None,
            "",
            "   ",
            "repo_for_README",
            "VELVET",
            "https://example.com/",
            "https://example.com",
        ],
    )
    def test_an_ownerless_or_unparseable_value_is_rejected(self, given) -> None:
        """The video incident's exact failure mode: the model invents ``repo_for_README`` for a
        local file it was actually asked to summarise. A bare word with no owner segment must
        never resolve to a repository."""
        assert RepositoryIdentity.parse_or_none(given) is None

    def test_whitespace_inside_a_segment_is_rejected(self) -> None:
        assert RepositoryIdentity.parse_or_none("ac me/widgets") is None


class TestNormalizeSlug:
    """The permissive, non-raising sibling `_normalize_repo` (`tools/coding.py`) delegates to --
    kept for callers that do their own shape check afterwards."""

    def test_a_valid_slug_normalizes_unchanged(self) -> None:
        assert RepositoryIdentity.normalize_slug("acme/widgets") == "acme/widgets"

    def test_a_url_normalizes_to_the_slug(self) -> None:
        assert (
            RepositoryIdentity.normalize_slug("https://github.com/acme/widgets.git")
            == "acme/widgets"
        )

    def test_an_ownerless_word_normalizes_to_itself_rather_than_raising(self) -> None:
        """Permissive by design -- `parse_or_none` is the strict check; this one never raises."""
        assert RepositoryIdentity.normalize_slug("repo_for_README") == "repo_for_README"
