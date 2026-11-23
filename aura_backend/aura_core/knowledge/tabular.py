"""Reading tables out of delimited text and spreadsheets, and describing their columns.

One implementation shared by the files *connector* (``adapters/connectors/files.py``, which turns a
spreadsheet export into a catalogued table) and the agent's ``inspect_spreadsheet`` tool
(``tools/media.py``), so a column has the same type whichever door it came in by. It lives here, in
``knowledge``, because a tool may import this layer and may not import an adapter (ADR-001).

What is inferred, and how strictly, is documented on the connector module: a column is
``boolean``, ``integer``, ``bigint``, ``numeric``, ``date`` or ``timestamp`` only when *every*
non-empty value parses as one, anything mixed is ``text``, and a number with a leading zero stays
text (it is a code, not a quantity). :func:`profile_column` adds what the tool reports on top of
that: counts, numeric summary statistics, date range and the most frequent values.

Pure functions over bytes and values: no file or network access, nothing to mock.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import statistics
from collections import Counter
from typing import Any, Sequence

__all__ = [
    "HEADER_SCAN", "cell_text", "coerce", "infer_type", "isoformat", "normalise_sheet_dates", "parse_cell",
    "profile_column", "read_delimited", "sheet_header", "sniff_delimiter", "friendly_type",
]

_INTEGER = re.compile(r"^-?(0|[1-9][0-9]*)$")
_NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?$")
_BOOLEAN = {"true": True, "false": False}
_INT32 = 2**31 - 1


def infer_type(values: Sequence[Any]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "text"
    if all(isinstance(v, bool) for v in present):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "integer" if all(abs(v) <= _INT32 for v in present) else "bigint"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "numeric"
    if all(isinstance(v, str) and _DATE.match(v) for v in present):
        return "date"
    if all(isinstance(v, str) and _TIMESTAMP.match(v) for v in present):
        has_zone = all(v.endswith("Z") or re.search(r"[+-][0-9]{2}:?[0-9]{2}$", v) for v in present)
        return "timestamp with time zone" if has_zone else "timestamp without time zone"
    return "text"


def parse_cell(text: str) -> Any:
    """A CSV cell as its natural JSON value: empty -> None, then bool, int, float, else text."""
    if text == "":
        return None
    lowered = text.lower()
    if lowered in _BOOLEAN:
        return _BOOLEAN[lowered]
    if _INTEGER.match(text):
        return int(text)
    if _NUMBER.match(text):
        return float(text)
    return text


def coerce(column_type: str, value: Any) -> Any:
    """A value under its column's inferred type; mixed columns are text, so their cells become text."""
    if value is None or column_type != "text" or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def read_delimited(data: bytes, delimiter: str = ",") -> tuple[list[str], list[list[Any]]]:
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader, None) or []
    rows = [[parse_cell(cell) for cell in row] for row in reader if any(cell.strip() for cell in row)]
    return [h.strip() for h in header], rows


#: Rows scanned from the top of a sheet for its header, and the slack the reader gets beyond
#: ``MAX_ROWS`` so a title block above the header does not eat into the data allowance.
HEADER_SCAN = 50


def cell_text(value: Any) -> str:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def sheet_header(rows: Sequence[Sequence[Any]]) -> tuple[list[str], list[list[Any]]]:
    """``(header, data rows)`` for a sheet's non-empty rows; synthesised names when no row is a header.

    The header is the first of the leading rows at least half as wide as the widest of them (which
    steps over a one-cell title line) provided most of its cells are text. A sheet whose first such
    row is numeric has no header: every row is data and the columns are ``column_1`` ... ``column_n``.
    """
    width = max((len(r) for r in rows), default=0)
    counts = [sum(1 for v in r if v is not None) for r in rows[:HEADER_SCAN]]
    widest = max(counts, default=0)
    start = next((i for i, c in enumerate(counts) if c >= max(1, -(-widest // 2))), 0)
    candidate = rows[start] if rows else ()
    present = [v for v in candidate if v is not None]
    texts = sum(1 for v in present if isinstance(v, str))
    if present and texts * 2 >= len(present):
        data = [list(r) for r in rows[start + 1:]]
        header = [(cell_text(candidate[j]) if j < len(candidate) and candidate[j] is not None else "")
                  for j in range(width)]
    else:
        data = [list(r) for r in rows]
        header = [""] * width
    for j, name in enumerate(header):
        if not name:
            # A blank header cell over data still names a column; one over nothing is just padding.
            header[j] = f"column_{j + 1}" if any(j < len(r) and r[j] is not None for r in data) else ""
    return header, data


def isoformat(moment: dt.datetime | dt.time) -> str:
    return moment.isoformat(timespec="milliseconds" if moment.microsecond else "seconds")


def normalise_sheet_dates(rows: list[list[Any]], columns: int) -> None:
    """Datetimes become ISO strings in place, the way a CSV export spells them (so the column is
    inferred ``date`` or ``timestamp`` by the same rules). A column is a ``date`` column only if no
    value in it carries a time of day; one that does makes every value in it a timestamp."""
    for j in range(columns):
        moments = [r[j] for r in rows if j < len(r) and isinstance(r[j], dt.datetime)]
        whole_days = all(m.time() == dt.time(0) for m in moments)
        for row in rows:
            if j >= len(row):
                continue
            value = row[j]
            if isinstance(value, dt.datetime):
                row[j] = value.date().isoformat() if whole_days else isoformat(value)
            elif isinstance(value, dt.time):
                row[j] = isoformat(value)


def sniff_delimiter(data: bytes) -> str:
    """``","``, ``";"``, ``"\\t"`` or ``"|"``: the one that splits the first lines into the most (and a
    consistent number of) columns. A comma wins ties, so an ordinary CSV is never second-guessed."""
    text = data[:65_536].decode("utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if not lines:
        return ","
    best, best_score = ",", -1.0
    for delimiter in (",", ";", "\t", "|"):
        counts = [len(next(csv.reader([line], delimiter=delimiter), [])) for line in lines]
        modal = Counter(counts).most_common(1)[0][0]
        if modal < 2:
            continue
        score = modal * (sum(1 for c in counts if c == modal) / len(counts))
        if score > best_score:  # comma is tried first, so it keeps every tie
            best, best_score = delimiter, score
    return best


_FRIENDLY = {
    "boolean": "boolean", "integer": "integer", "bigint": "integer", "numeric": "number", "date": "date",
    "timestamp with time zone": "datetime", "timestamp without time zone": "datetime", "text": "text",
}


def friendly_type(column_type: str) -> str:
    """The catalog's Postgres-flavoured type name as the plain word the agent is shown."""
    return _FRIENDLY.get(column_type, column_type)


def _clip(value: Any, limit: int = 80) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _round(value: float) -> float | int:
    if not math.isfinite(value):
        return value
    return int(value) if float(value).is_integer() and abs(value) < 1e15 else round(value, 6)


def profile_column(values: Sequence[Any], column_type: str, *, top_n: int = 5) -> dict[str, Any]:
    """Counts and summary statistics for one column of cell values (``None`` = empty).

    Always: ``non_null``, ``null``, ``distinct``. By type: numeric columns get ``min``, ``max``,
    ``mean``, ``median`` and ``stddev`` (sample standard deviation; ``None`` for a single value);
    dates and timestamps get ``min`` and ``max``; booleans get the true/false split; text gets its
    ``top_values`` with counts (the whole distribution when it is small, so a categorical column is
    readable at a glance) and the shortest/longest value length. A text column in which nearly every
    value is numeric but a few are not says so in ``note`` -- usually the stray ``n/a`` or ``-`` that
    stopped the column from being typed as a number.
    """
    present = [v for v in values if v is not None]
    out: dict[str, Any] = {"non_null": len(present), "null": len(values) - len(present)}
    keys = [json.dumps(v, sort_keys=True, default=str) for v in present]
    out["distinct"] = len(set(keys))
    friendly = friendly_type(column_type)
    if friendly in ("integer", "number") and present:
        numbers = [float(v) for v in present if not isinstance(v, bool)]
        out["min"] = _round(min(numbers))
        out["max"] = _round(max(numbers))
        out["mean"] = _round(statistics.fmean(numbers))
        out["median"] = _round(statistics.median(numbers))
        out["stddev"] = _round(statistics.stdev(numbers)) if len(numbers) > 1 else None
    elif friendly in ("date", "datetime") and present:
        ordered = sorted(str(v) for v in present)
        out["min"], out["max"] = ordered[0], ordered[-1]
    elif friendly == "boolean" and present:
        out["true"] = sum(1 for v in present if v is True)
        out["false"] = sum(1 for v in present if v is False)
    elif present:
        texts = [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str) for v in present]
        out["top_values"] = [{"value": _clip(value), "count": count} for value, count in Counter(texts).most_common(top_n)]
        lengths = [len(t) for t in texts]
        out["min_length"], out["max_length"] = min(lengths), max(lengths)
        numeric = [t for t in texts if _NUMBER.match(t.strip().replace(",", ""))]
        if numeric and 0.8 <= len(numeric) / len(texts) < 1:
            odd = sorted({_clip(t, 30) for t in texts if t not in set(numeric)})[:3]
            out["note"] = (f"{len(numeric)} of {len(texts)} values are numeric, so this column is typed text only "
                           f"because of values such as {odd}")
    return out
