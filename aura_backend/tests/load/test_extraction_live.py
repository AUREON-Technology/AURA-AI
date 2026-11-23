"""Live check of ``extract_structured``'s service, Cloudflare's ``clairic`` (ADR-038).

Opt-in, like every ``-m load`` test: it spends real Workers AI allowance and needs a real token. It
exists to produce the evidence ADR-038 / GAP-068 asks for and the default suite cannot:

* the adapter works against the real endpoint (the request it builds is accepted, the response it
  parses is the one the service sends);
* **the invariant the whole design rests on**: on a labelled English set, no field is returned as an
  answer (``ok``) and wrong. A wrong value must come back withheld (``low_confidence``), never as a
  confident answer;
* a measured per-field table, English and Arabic, printed for ``docs/reports/extract-structured-*.md``.
  Arabic is measured, not asserted: the service does not state its Arabic support (ADR-038 O-3).

Run it::

    $env:AURA_EXTRACT_LIVE_ACCOUNT_ID = "<32-hex Cloudflare account id>"
    $env:AURA_EXTRACT_LIVE_TOKEN = "<a Workers AI token>"
    python -m pytest tests/load/test_extraction_live.py -m load -s

About ten calls of roughly 2,500 input tokens each: a few hundred neurons, a few percent of the
10,000-neuron daily allowance the ``hosted3`` chat slot shares. The token is read from the
environment and never printed.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from aura_core.adapters.cloudflare_clairic import ClairicExtractor
from aura_core.adapters.runtime import SystemClock
from aura_core.domain.extraction import ExtractionRequest, FieldSpec, FieldStatus, FieldType

pytestmark = pytest.mark.load

_REQUIRED = ("AURA_EXTRACT_LIVE_ACCOUNT_ID", "AURA_EXTRACT_LIVE_TOKEN")
MIN_CONFIDENCE = 0.5

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")


def _spec(name: str, kind: str, instructions: str = "", **kw: Any) -> FieldSpec:
    return FieldSpec(name, FieldType(kind), instructions=instructions, **kw)


#: (case id, language, text, field specs, expected value per field; None = the text does not say)
CASES: list[tuple[str, str, str, list[FieldSpec], dict[str, Any]]] = [
    (
        "en-invoice", "en",
        "Invoice INV-2291 from Northwind Supply, issued 2026-09-02, net 30. 4 pallets at $250.00 "
        "each, shipping $80.00. Contact: Dana Ruiz, Priya Shah.",
        [_spec("invoice_number", "text"), _spec("issue_date", "date", "date the invoice was issued"),
         _spec("total", "number", "amount due including shipping", decimals=2),
         _spec("contacts", "text_list"), _spec("po_number", "text", "purchase order number")],
        {"invoice_number": "INV-2291", "issue_date": "2026-09-02", "total": 1080,
         "contacts": ["Dana Ruiz", "Priya Shah"], "po_number": None},
    ),
    (
        "en-bill", "en",
        "Bill no. B-5521 dated 3 March 2026. Subtotal 1,000.00 EUR, VAT 20% = 200.00 EUR. "
        "Total payable: 1,200.00 EUR. Purchase order: PO-88.",
        [_spec("invoice_number", "text"), _spec("issue_date", "date", "date the bill is dated"),
         _spec("total", "number", "total payable", decimals=2), _spec("po_number", "text")],
        {"invoice_number": "B-5521", "issue_date": "2026-03-03", "total": 1200, "po_number": "PO-88"},
    ),
    (
        "en-receipt", "en",
        "Receipt #4410 - paid in full, thank you! Date: 12/11/2026 (DD/MM/YYYY). Amount: 75.00 GBP.",
        [_spec("receipt_number", "text"), _spec("date", "date", "the receipt date; the text says DD/MM/YYYY"),
         _spec("amount", "number", decimals=2), _spec("paid", "boolean", "whether it was paid in full")],
        {"receipt_number": "4410", "date": "2026-11-12", "amount": 75, "paid": True},
    ),
    (
        "en-nothing", "en",
        "Please find attached the document we discussed. Regards, Sam",
        [_spec("invoice_number", "text"), _spec("total", "number"), _spec("issue_date", "date")],
        {"invoice_number": None, "total": None, "issue_date": None},
    ),
    (
        "ar-invoice", "ar",
        "فاتورة رقم INV-3310 صادرة بتاريخ 2026-09-10 من شركة النيل للتوريدات. "
        "الإجمالي المستحق: 2,450.50 جنيه.",
        [_spec("invoice_number", "text"), _spec("issue_date", "date", "تاريخ إصدار الفاتورة"),
         _spec("total", "number", "الإجمالي المستحق", decimals=2), _spec("supplier", "text", "اسم المورد")],
        {"invoice_number": "INV-3310", "issue_date": "2026-09-10", "total": 2450.5,
         "supplier": "النيل للتوريدات"},
    ),
    (
        "ar-receipt-indic-digits", "ar",
        "إيصال رقم ٧٧٢١ بتاريخ ٢٠٢٦-١٠-٠٥. المبلغ المدفوع ١٬٢٠٠٫٠٠ ريال.",
        [_spec("receipt_number", "text"), _spec("date", "date", "تاريخ الإيصال"),
         _spec("amount", "number", "المبلغ المدفوع", decimals=2)],
        {"receipt_number": "7721", "date": "2026-10-05", "amount": 1200},
    ),
    (
        "ar-nothing", "ar",
        "شكرا لتواصلكم. سنرد عليكم قريبا.",
        [_spec("invoice_number", "text"), _spec("total", "number")],
        {"invoice_number": None, "total": None},
    ),
]


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return value.translate(_ARABIC_INDIC).strip().casefold()
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _matches(expected: Any, got: Any, kind: FieldType) -> bool:
    if expected is None or got is None:
        return expected is None and got is None
    if kind is FieldType.NUMBER:
        return abs(float(expected) - float(got)) < 0.005
    if kind is FieldType.TEXT:
        e, g = _norm(expected), _norm(got)
        return e in g or g in e  # a supplier named with or without its "company" word is still right
    return _norm(expected) == _norm(got)


@pytest.fixture(scope="module")
def extractor() -> ClairicExtractor:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live extraction check not configured, missing: {', '.join(missing)}")
    return ClairicExtractor(
        account_id=os.environ["AURA_EXTRACT_LIVE_ACCOUNT_ID"],
        api_key=os.environ["AURA_EXTRACT_LIVE_TOKEN"],
        clock=SystemClock(),
    )


_ROWS: list[dict[str, Any]] | None = None


async def _measured(extractor: ClairicExtractor) -> list[dict[str, Any]]:
    """Every field of every case, classified, measured once for the whole module.

    A plain cached helper rather than a module-scoped async fixture: those need the event loop's
    scope to match the fixture's, which is fragile across pytest-asyncio versions. One call per
    case; failures are not retried.
    """
    global _ROWS
    if _ROWS is not None:
        return _ROWS
    rows: list[dict[str, Any]] = []
    for case_id, lang, text, specs, expected in CASES:
        result = await extractor.extract(
            ExtractionRequest(text=text, fields=tuple(specs)), min_confidence=MIN_CONFIDENCE
        )
        for spec in specs:
            field = result.fields[spec.name]
            want = expected[spec.name]
            if field.status is FieldStatus.OK:
                outcome = "correct" if _matches(want, field.value, spec.type) else "CONFIDENTLY WRONG"
            elif field.status is FieldStatus.NULL:
                outcome = "correct (null)" if want is None else "missed (null)"
            elif field.status is FieldStatus.LOW_CONFIDENCE:
                outcome = (
                    "withheld, guess right" if _matches(want, field.guess, spec.type) else "withheld, guess wrong"
                )
            else:
                outcome = f"invalid ({field.reason})"
            rows.append({
                "case": case_id, "lang": lang, "field": spec.name, "expected": want,
                "status": field.status.value, "value": field.value, "guess": field.guess,
                "confidence": field.confidence, "outcome": outcome,
            })
    await extractor.close()
    _ROWS = rows
    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    print("\n\ncase / field / expected / status / value-or-guess / confidence / outcome")
    for r in rows:
        shown = r["value"] if r["status"] == "ok" else r["guess"]
        conf = "-" if r["confidence"] is None else f"{r['confidence']:.3f}"
        print(f"{r['case']:<24} {r['field']:<15} {str(r['expected']):<24} {r['status']:<15} "
              f"{str(shown):<26} {conf:<6} {r['outcome']}")
    for lang in ("en", "ar"):
        mine = [r for r in rows if r["lang"] == lang]
        tally: dict[str, int] = {}
        for r in mine:
            tally[r["outcome"]] = tally.get(r["outcome"], 0) + 1
        print(f"\n{lang}: {len(mine)} fields -> " + ", ".join(f"{k}: {v}" for k, v in sorted(tally.items())))


async def test_the_real_endpoint_answers_every_field_of_every_case(extractor) -> None:
    measured = await _measured(extractor)
    _print_table(measured)
    assert len(measured) == sum(len(c[3]) for c in CASES), "every declared field must come back classified"
    assert not [r for r in measured if r["status"] == "invalid"], (
        "an invalid answer means the adapter and the live response disagree: "
        + str([(r["case"], r["field"], r["outcome"]) for r in measured if r["status"] == "invalid"])
    )


async def test_english_is_never_confidently_wrong(extractor) -> None:
    """The invariant ADR-038 rests on: a wrong value must arrive withheld, never as an answer."""
    measured = await _measured(extractor)
    wrong =[(r["case"], r["field"], r["expected"], r["value"], r["confidence"])
             for r in measured if r["lang"] == "en" and r["outcome"] == "CONFIDENTLY WRONG"]
    assert not wrong, (
        "the service returned a wrong English value above the confidence threshold; raise "
        f"AURA_EXTRACT_MIN_CONFIDENCE or reconsider the tool (ADR-038 §4): {wrong}"
    )


async def test_arabic_is_measured_and_reported_not_asserted(extractor) -> None:
    """ADR-038 O-3: Cloudflare does not state Arabic support, so this only records the numbers."""
    measured = await _measured(extractor)
    arabic =[r for r in measured if r["lang"] == "ar"]
    assert arabic, "the Arabic cases must have run"
    wrong = [r for r in arabic if r["outcome"] == "CONFIDENTLY WRONG"]
    if wrong:
        print(f"\nWARNING: {len(wrong)} Arabic field(s) were confidently wrong: "
              + str([(r["case"], r["field"]) for r in wrong]))
