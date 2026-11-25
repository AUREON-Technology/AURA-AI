"""Format, corruption, outlier and type checks for ``validate_data_records`` (ADR-046, row 102).

The tool already finds blanks, duplicates and numeric bounds. This adds the other things "data quality
before ingestion, training or retrieval" asks for, over rows the caller passes (a list of objects):

* ``check_formats`` -- per string column, the dominant *shape* of its values (email, UUID, URL, ISO date
  or datetime, other date layouts, integer, decimal, boolean word, phone, or free text) and every value
  that departs from it, plus stray whitespace and case variants of one category (``Active`` / ``active ``).
  A column can also be pinned to a format with ``formats={column: kind}``.
* ``detect_corrupted`` -- mojibake (UTF-8 read as Latin-1/CP1252), the replacement character, NUL and
  control characters, truncated JSON text, impossible calendar dates, rows whose keys differ from the
  rest (ragged), rows that are not objects, and absurdly long values.
* ``detect_outliers`` -- z-score (the existing check), or the robust IQR / median-absolute-deviation
  rules, which a single extreme value cannot hide behind.
* ``check_types`` -- type contracts, or with none given an inferred contract and the rows that break it
  (a column that is 98% numbers and has ``"n/a"`` strings).
* ``profile`` -- all of the above plus missing values and duplicates, with one 0-100 score. The score is
  a summary of the counts, not a guarantee; the counts are what to read.

Pure text-in/JSON-out; a hook that runs this before an ingestion job is a separate piece of work (nothing
in the platform calls it automatically).
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from .data_quality_and_validation_engine import DataQualityAndValidationEngine as Engine

__all__ = ["check_formats", "detect_corrupted", "detect_outliers", "check_types", "profile", "FORMATS"]

_MAX_SAMPLES = 10
_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "url": re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE),
    "iso_datetime": re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$"),
    "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "slash_date": re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    "integer": re.compile(r"^[+-]?\d+$"),
    "decimal": re.compile(r"^[+-]?\d*\.\d+$"),
    "decimal_comma": re.compile(r"^[+-]?\d+,\d+$"),
    "boolean_word": re.compile(r"^(true|false|yes|no|y|n|0|1)$", re.IGNORECASE),
    "phone": re.compile(r"^\+?[\d\s().-]{7,20}$"),
}
FORMATS = tuple(_PATTERNS) + ("text",)
_ORDER = ("uuid", "email", "url", "iso_datetime", "iso_date", "slash_date", "decimal", "decimal_comma", "integer",
          "boolean_word", "phone")
_MOJIBAKE = re.compile(r"Ã[\x80-\xbf¡-¿]|â€[™œ\x9d\x9cš¦“”˜]|Â[\xa0-\xbf]|ï»¿|Ã©|Ã¨|Ã¼")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_VALUE_CHARS = 100_000


def _rows(records: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(i, r) for i, r in enumerate(records) if isinstance(r, dict)]


def _shape(value: str) -> str:
    text = value.strip()
    if not text:
        return "empty"
    for name in _ORDER:
        if _PATTERNS[name].match(text):
            return name
    return "text"


def check_formats(records: list[Any], formats: dict[str, str] | None = None) -> dict[str, Any]:
    rows = _rows(records)
    formats = formats or {}
    unknown = {c: f for c, f in formats.items() if f not in FORMATS}
    columns: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, row in rows:
        for column, value in row.items():
            if isinstance(value, str):
                columns[str(column)].append((index, value))
    report: dict[str, Any] = {}
    issues = 0
    for column, values in columns.items():
        pinned = formats.get(column)
        if pinned in _PATTERNS:
            bad = [(i, v) for i, v in values if v.strip() and not _PATTERNS[pinned].match(v.strip())]
            report[column] = {"expected": pinned, "checked": len(values), "violations": len(bad),
                              "samples": [{"row": i, "value": v[:80]} for i, v in bad[:_MAX_SAMPLES]]}
            issues += len(bad)
            continue
        shapes = Counter(_shape(v) for _, v in values if v.strip())
        entry: dict[str, Any] = {"checked": len(values)}
        if shapes:
            dominant, count = shapes.most_common(1)[0]
            share = count / sum(shapes.values())
            entry["dominant_format"] = dominant
            entry["dominant_share"] = round(share, 3)
            if dominant != "text" and share >= 0.6 and share < 1.0:
                bad = [(i, v) for i, v in values if v.strip() and _shape(v) != dominant]
                entry["violations"] = len(bad)
                entry["other_formats"] = dict(shapes)
                entry["samples"] = [{"row": i, "value": v[:80], "looks_like": _shape(v)} for i, v in bad[:_MAX_SAMPLES]]
                issues += len(bad)
            elif dominant in ("iso_date", "slash_date", "iso_datetime") and len({s for s in shapes if "date" in s}) > 1:
                entry["violations"] = sum(c for s, c in shapes.items() if s != dominant)
                entry["other_formats"] = dict(shapes)
                issues += entry["violations"]
        padded = [(i, v) for i, v in values if v != v.strip() and v.strip()]
        if padded:
            entry["whitespace_padded"] = {"count": len(padded), "samples": [{"row": i, "value": repr(v[:60])} for i, v in padded[:5]]}
            issues += len(padded)
        distinct = {v for _, v in values if v.strip()}
        if 1 < len(distinct) <= 50 and len(distinct) < len(values):
            folded: dict[str, set[str]] = defaultdict(set)
            for v in distinct:
                folded[re.sub(r"\s+", " ", v.strip().casefold())].add(v)
            variants = {k: sorted(vs) for k, vs in folded.items() if len(vs) > 1}
            if variants:
                entry["case_or_spacing_variants"] = dict(list(variants.items())[:5])
                issues += len(variants)
        if len(entry) > 1 and (entry.get("violations") or "whitespace_padded" in entry or "case_or_spacing_variants" in entry):
            report[column] = entry
    result = {"columns_checked": len(columns), "columns_with_issues": len(report), "issues": issues, "columns": report,
              "consistent": issues == 0 and not unknown}
    if unknown:
        result["error"] = f"unknown format(s) {unknown}; use one of {list(FORMATS)}"
    return result


def _impossible_date(text: str) -> bool:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text.strip())
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return False
    except ValueError:
        return True


def detect_corrupted(records: list[Any]) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    def add(kind: str, index: int, column: str | None, value: Any, detail: str = "") -> None:
        counts[kind] += 1
        if len(found) < 50:
            found.append({"kind": kind, "row": index, "column": column, "value": repr(value)[:80], **({"detail": detail} if detail else {})})

    keysets = Counter(frozenset(map(str, r)) for r in records if isinstance(r, dict))
    common = keysets.most_common(1)[0][0] if keysets else frozenset()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            add("not_an_object", index, None, row, f"row is {type(row).__name__}")
            continue
        if keysets and frozenset(map(str, row)) != common and keysets[common] > 1:
            missing, extra = sorted(common - set(map(str, row))), sorted(set(map(str, row)) - common)
            add("ragged_row", index, None, "", f"missing {missing[:5]}, extra {extra[:5]}")
        for column, value in row.items():
            if isinstance(value, str):
                if "�" in value:
                    add("replacement_character", index, str(column), value, "U+FFFD: bytes were lost when decoding")
                if _MOJIBAKE.search(value):
                    add("mojibake", index, str(column), value, "UTF-8 text decoded as Latin-1/CP1252")
                if _CONTROL.search(value):
                    add("control_characters", index, str(column), value, "NUL or other control characters")
                if len(value) > _MAX_VALUE_CHARS:
                    add("oversized_value", index, str(column), value[:40], f"{len(value)} characters")
                stripped = value.strip()
                if stripped[:1] in "{[" and stripped:
                    try:
                        json.loads(stripped)
                    except ValueError:
                        if stripped.count("{") != stripped.count("}") or stripped.count("[") != stripped.count("]"):
                            add("truncated_json", index, str(column), value, "unbalanced braces: the text was cut off")
                if _impossible_date(value):
                    add("impossible_date", index, str(column), value, "no such calendar day")
            elif isinstance(value, float) and not math.isfinite(value):
                add("non_finite_number", index, str(column), value)
    return {"corrupted_count": sum(counts.values()), "by_kind": dict(counts), "findings": found,
            "clean": not counts, "rows_checked": len(records)}


def detect_outliers(records: list[Any], field: str, method: str = "zscore", threshold: float | None = None) -> dict[str, Any]:
    if method == "zscore":
        return Engine.detect_statistical_outliers(records, field, z_score_threshold=threshold or 3.0)
    values = [(i, r[field]) for i, r in _rows(records) if field in r
              and isinstance(r[field], (int, float)) and not isinstance(r[field], bool) and math.isfinite(r[field])]
    if len(values) < 4:
        return {"field": field, "method": method, "outliers_count": 0, "outliers": [],
                "error": "need at least 4 numeric values for a robust outlier rule"}
    nums = [v for _, v in values]
    if method == "iqr":
        q1, _, q3 = statistics.quantiles(nums, n=4)
        k = threshold or 1.5
        low, high = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
        out = [{"row": i, "value": v, "reason": "below lower fence" if v < low else "above upper fence"}
               for i, v in values if v < low or v > high]
        bounds = {"lower_fence": low, "upper_fence": high}
    else:  # mad
        median = statistics.median(nums)
        mad = statistics.median(abs(v - median) for v in nums)
        k = threshold or 3.5
        if mad == 0:
            out = [{"row": i, "value": v, "reason": "differs from a constant column"} for i, v in values if v != median]
        else:
            out = [{"row": i, "value": v, "modified_z": round(0.6745 * (v - median) / mad, 2)}
                   for i, v in values if abs(0.6745 * (v - median) / mad) > k]
        bounds = {"median": median, "mad": mad}
    return {"field": field, "method": method, "threshold": k, "values_checked": len(nums), "outliers_count": len(out),
            "outliers": out[:50], **bounds}


def check_types(records: list[Any], type_contracts: dict[str, str] | None = None) -> dict[str, Any]:
    if type_contracts:
        return Engine.validate_types_and_schemas(records, type_contracts)
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    for _, row in _rows(records):
        for column, value in row.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            name = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else \
                "number" if isinstance(value, float) else "string" if isinstance(value, str) else \
                "array" if isinstance(value, list) else "object" if isinstance(value, dict) else type(value).__name__
            if name == "string" and _PATTERNS["integer"].match(value.strip()):
                name = "numeric_string"
            kinds[str(column)][name] += 1
    inferred, violations = {}, []
    for column, counter in kinds.items():
        merged = Counter({("number" if k in ("integer", "number") else k): 0 for k in counter})
        for k, c in counter.items():
            merged["number" if k in ("integer", "number") else k] += c
        dominant, count = merged.most_common(1)[0]
        inferred[column] = dominant
        if len(merged) > 1 and count / sum(merged.values()) >= 0.6:
            violations.append({"column": column, "dominant_type": dominant, "types_seen": dict(merged)})
    return {"inferred_contract": inferred, "mixed_type_columns": violations, "schema_compliant": not violations,
            "note": "no type_contracts given: the contract was inferred from the data's majority, so only mixed columns are reported"}


def profile(records: list[Any], formats: dict[str, str] | None = None, type_contracts: dict[str, str] | None = None,
            required_fields: list[str] | None = None, unique_key: str | None = None) -> dict[str, Any]:
    rows = _rows(records)
    base = Engine.validate_records(list(records), required_fields=required_fields or [], unique_key=unique_key)
    missing = Engine.detect_missing_values(list(records))
    dupes = Engine.detect_duplicates(list(records))
    fmt, bad, types = check_formats(records, formats), detect_corrupted(records), check_types(records, type_contracts)
    total_rows = max(len(records), 1)
    missing_cells = sum(c["missing_count"] for c in missing.get("columns", {}).values())
    cells = total_rows * max(len(missing.get("columns", {})), 1)
    penalties = {
        "missing_values": round(30 * missing_cells / cells, 2),
        "duplicates": round(15 * min(dupes["duplicates_count"] / total_rows, 1), 2),
        "format_issues": round(20 * min(fmt["issues"] / total_rows, 1), 2),
        "corruption": round(25 * min(bad["corrupted_count"] / total_rows, 1), 2),
        "type_issues": round(10 * min(len(types.get("mixed_type_columns", types.get("violations", []))) / max(len(rows[0][1]) if rows else 1, 1), 1), 2),
    }
    score = round(max(0.0, 100.0 - sum(penalties.values())), 1)
    return {"rows": len(records), "score": score, "penalties": penalties, "ready_for_ingestion": score >= 90 and bad["clean"],
            "records": base, "missing": {k: v for k, v in missing.get("columns", {}).items() if v["missing_count"]},
            "duplicates": {"count": dupes["duplicates_count"], "examples": dupes["duplicates"][:5]},
            "formats": fmt, "corruption": bad, "types": types}
