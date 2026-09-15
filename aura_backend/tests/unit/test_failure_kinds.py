"""``FailureKind``: the closed vocabulary `error_kind` strings are drawn from (Item 6).

A `str` enum member must compare equal to its value -- that is what lets existing call sites
keep spelling the literal string (e.g. `"unresolved_argument"`) while newer code uses the enum,
without a flag day migrating every site at once.
"""

from __future__ import annotations

from aura_core.domain.failure_kinds import FailureKind


class TestFailureKind:
    def test_members_compare_equal_to_their_literal_string(self) -> None:
        assert FailureKind.UNRESOLVED_ARGUMENT == "unresolved_argument"
        assert FailureKind.UNRESOLVED_REFERENCE == "unresolved_reference"

    def test_the_two_new_kinds_this_hardening_introduces_are_present(self) -> None:
        values = {member.value for member in FailureKind}
        assert "unresolved_argument" in values
        assert "unresolved_reference" in values

    def test_membership_has_no_duplicate_values(self) -> None:
        values = [member.value for member in FailureKind]
        assert len(values) == len(set(values))
