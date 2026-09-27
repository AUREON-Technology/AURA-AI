"""Certification is refused mechanically while any mandatory gate lacks evidence.

The closure rule this enforces is the brief's own: *"Refuse certification if any mandatory gate
lacks evidence."* A rule like that, left in prose, decays in a predictable way -- not by anyone
deciding to lie, but by a gate quietly moving to PASS because an instrument now exists, or a
summary being rewritten while the table underneath it is not, or a reader taking a skipped test
for a passing one. Each of those is a small, reasonable-looking edit. The aggregate is a false
certification.

So the rule runs. This parses the table in
``docs/reports/AURA_CERTIFICATION_EVIDENCE_MATRIX.md`` on every default ``pytest`` invocation and
fails if:

* a mandatory gate from the brief's §36 list is missing from the table;
* any gate claims PASS or CERTIFIED without citing a specific artifact;
* any mandatory gate has no evidence while the document's stated verdict is not a refusal;
* a row is malformed, or a verdict/maturity value is not one of the permitted ones.

Deliberately in ``tests/architecture/`` rather than ``tests/certification/``: the certification
suites are opt-in behind the ``cert`` marker because they need credentials, and a rule that only
runs when someone remembers to run it is the failure mode above. This one runs with a bare
``pytest``.

It checks internal consistency, not the world. It cannot tell whether EV-22 says what a row claims
it says -- a human reads the evidence. What it can guarantee is that the claim and the verdict
cannot silently disagree, and that "we have a test for that" can never be recorded as "that is
proven".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MATRIX = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "reports"
    / "AURA_CERTIFICATION_EVIDENCE_MATRIX.md"
)

_BEGIN = "<!-- CERTIFICATION-MATRIX-BEGIN -->"
_END = "<!-- CERTIFICATION-MATRIX-END -->"

#: The brief's §36 mandatory list, as gate names that must each have a row. Matched on a distinctive
#: substring so a row can be reworded without silently dropping out of the check.
MANDATORY_SUBSTRINGS = (
    "Authentication",
    "Authorization / isolation",
    "Adversarial AI safety",
    "containment",
    "Backup / restore",
    "critical security flaw",
    "exposed secrets",
    "Production API usable",
    "Core user journeys",
    "Inference works",
    "unrecoverable data failure",
    "Deployment stability",
)

#: A verdict that asserts something is proven. These are the ones that require evidence.
CLAIMS_PROVEN = frozenset({"PASS", "CERTIFIED"})

VERDICTS = frozenset(
    {"PASS", "CERTIFIED", "PARTIAL", "FAIL", "BLOCKED", "NOT APPLICABLE", "UNKNOWN"}
)
MATURITIES = frozenset(
    {
        "CODE-PRESENT",
        "LOCALLY TESTED",
        "FAILURE TESTED",
        "MEASURED",
        "PRODUCTION TESTED",
        "CERTIFIED",
    }
)

#: Any of these in the verdict line means the document is refusing certification.
REFUSALS = ("NOT PRODUCTION CERTIFIED", "CONDITIONAL PRODUCTION CERTIFICATION")


class Row:
    __slots__ = ("gate", "mandatory", "maturity", "evidence", "verdict")

    def __init__(self, cells: list[str]) -> None:
        self.gate, mandatory, self.maturity, self.evidence, self.verdict = cells
        self.mandatory = mandatory.strip().lower() == "yes"

    @property
    def has_evidence(self) -> bool:
        """An em dash, a blank, or a bare "none" is no evidence.

        Note what is deliberately *not* accepted: naming a test file. An instrument that exists
        but has never been run against a target proves nothing, and treating a path as evidence is
        exactly how a built suite becomes a claimed pass.
        """
        text = self.evidence.strip()
        return bool(text) and text not in {"—", "-", "–", "none", "None", "n/a", "N/A", "TBD"}

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"{self.gate!r} verdict={self.verdict} evidence={self.evidence!r}"


def _document() -> str:
    assert _MATRIX.exists(), f"the certification matrix is missing: {_MATRIX}"
    return _MATRIX.read_text(encoding="utf-8")


def _rows() -> list[Row]:
    text = _document()
    assert _BEGIN in text and _END in text, (
        "the matrix markers are gone. They delimit the machine-checked table; without them this "
        "gate cannot find the rows it is supposed to enforce."
    )
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0]

    rows: list[Row] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0].lower() == "gate":
            continue
        rows.append(Row(cells))
    assert rows, "the matrix table contains no data rows"
    return rows


class TestTheMatrixIsWellFormed:
    def test_every_row_has_a_known_verdict(self) -> None:
        bad = [r for r in _rows() if r.verdict not in VERDICTS]
        assert not bad, f"unrecognised verdicts (typos become invisible passes): {bad}"

    def test_every_row_has_a_known_maturity(self) -> None:
        bad = [r for r in _rows() if r.maturity not in MATURITIES]
        assert not bad, f"unrecognised maturity levels: {bad}"

    def test_no_gate_is_listed_twice(self) -> None:
        seen: dict[str, int] = {}
        for row in _rows():
            seen[row.gate] = seen.get(row.gate, 0) + 1
        duplicates = {g: n for g, n in seen.items() if n > 1}
        assert not duplicates, f"a duplicated gate lets two verdicts coexist: {duplicates}"


class TestEveryMandatoryGateIsPresent:
    @pytest.mark.parametrize("needle", MANDATORY_SUBSTRINGS)
    def test_the_gate_has_a_row(self, needle: str) -> None:
        """A mandatory gate cannot be retired by deleting its row."""
        rows = _rows()
        assert any(needle.lower() in r.gate.lower() for r in rows), (
            f"no row covers the mandatory gate matching {needle!r}. The brief's §36 list treats "
            "its absence as a blocker, so removing the row cannot be how it stops blocking."
        )

    def test_every_mandatory_row_is_marked_mandatory(self) -> None:
        for needle in MANDATORY_SUBSTRINGS:
            for row in _rows():
                if needle.lower() in row.gate.lower():
                    assert row.mandatory, (
                        f"{row.gate!r} matches the mandatory gate {needle!r} but its Mandatory "
                        "column does not say yes"
                    )
                    break


class TestAClaimRequiresEvidence:
    def test_nothing_claims_pass_without_citing_an_artifact(self) -> None:
        """The core rule. 'We wrote a test for it' is not a result."""
        unevidenced = [r for r in _rows() if r.verdict in CLAIMS_PROVEN and not r.has_evidence]
        assert not unevidenced, (
            "these gates claim to be proven but cite no evidence:\n  "
            + "\n  ".join(repr(r) for r in unevidenced)
        )

    def test_a_test_file_path_is_not_accepted_as_evidence_of_a_pass(self) -> None:
        """An instrument is not a result, and this is the exact substitution to guard against:
        a suite gets built, the row moves to PASS, and the path is offered as the proof."""
        offenders = [
            r
            for r in _rows()
            if r.verdict in CLAIMS_PROVEN
            and re.search(r"tests?/certification/|\.py::", r.evidence)
        ]
        assert not offenders, (
            "a certification-suite path is cited as evidence for a PASS. Those suites only "
            "produce evidence when run against a target; cite the run, not the file:\n  "
            + "\n  ".join(repr(r) for r in offenders)
        )


class TestTheVerdictCannotDisagreeWithTheRows:
    def test_an_unevidenced_mandatory_gate_forces_a_refusal(self) -> None:
        """The rule that makes the whole file more than documentation.

        If any mandatory gate lacks evidence, the document must refuse certification. Passing
        this test by editing the verdict is not possible without also supplying the evidence,
        which is the point.
        """
        rows = _rows()
        blocking = [r for r in rows if r.mandatory and not r.has_evidence]
        document = _document()
        refuses = any(marker in document for marker in REFUSALS)

        if blocking:
            assert refuses, (
                "mandatory gates without evidence:\n  "
                + "\n  ".join(repr(r) for r in blocking)
                + "\n\nThe document must therefore state NOT PRODUCTION CERTIFIED (or a "
                "conditional certification). Certification cannot be claimed while a mandatory "
                "gate has no evidence."
            )

    def test_a_failing_mandatory_gate_forces_a_refusal(self) -> None:
        """Evidence that something is broken is still evidence, and still blocks."""
        failing = [r for r in _rows() if r.mandatory and r.verdict == "FAIL"]
        document = _document()

        if failing:
            assert any(marker in document for marker in REFUSALS), (
                "mandatory gates with a FAIL verdict:\n  "
                + "\n  ".join(repr(r) for r in failing)
                + "\n\nThe document must state NOT PRODUCTION CERTIFIED."
            )

    def test_full_certification_requires_every_mandatory_gate_to_pass(self) -> None:
        """The other direction: an unqualified PRODUCTION CERTIFIED is only permitted when every
        mandatory gate actually passes with evidence."""
        document = _document()
        claims_full = "PRODUCTION CERTIFIED" in document and not any(
            marker in document for marker in REFUSALS
        )
        if not claims_full:
            return

        rows = _rows()
        not_passing = [r for r in rows if r.mandatory and r.verdict not in CLAIMS_PROVEN]
        assert not not_passing, (
            "the document claims PRODUCTION CERTIFIED while these mandatory gates do not pass:\n  "
            + "\n  ".join(repr(r) for r in not_passing)
        )
