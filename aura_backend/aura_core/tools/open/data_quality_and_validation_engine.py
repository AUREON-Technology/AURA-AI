import math
from typing import Any, Dict, List, Optional

#: Violation lists longer than this are cut (the ``*_count`` keys always hold the true total).
_MAX_LISTED = 1000


def _is_missing(value: Any) -> bool:
    """None, NaN, an empty string or a whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def _as_float(value: Any) -> Optional[float]:
    """``float(value)`` for numbers and numeric strings (booleans excluded); None when it cannot be."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _rows(records: Any) -> Optional[List[Any]]:
    return list(records) if isinstance(records, (list, tuple)) else None


class DataQualityAndValidationEngine:
    """Engine for checking null counts, duplicate records, schema types, outlier detection, and numeric bounds.

    The validators fail closed: no records, nothing to validate against, or a rule they do not
    understand is reported as invalid with an ``error``, never as "data valid".
    """

    @staticmethod
    def validate_records(
        records: List[Dict[str, Any]],
        required_fields: Optional[List[str]] = None,
        unique_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate a list of row dicts for missing/null required fields and duplicate unique_key values.

        Blank means ``None``, NaN, ``""`` or whitespace only. A row that has no value for
        ``unique_key`` is counted in ``missing_unique_key_count`` (it cannot be shown to be unique).
        With no rows, or with neither ``required_fields`` nor ``unique_key``, nothing was
        validated: ``data_valid`` is False and ``error`` says so.
        """
        rows = _rows(records)
        req = [str(f) for f in required_fields] if isinstance(required_fields, (list, tuple)) else []
        if rows is None or not rows or (not req and not unique_key):
            error = (
                "No records were supplied, so nothing was validated."
                if not rows
                else "No required_fields or unique_key were given, so nothing was validated."
            )
            return {
                "data_valid": False,
                "error": error,
                "rows_checked": len(rows or []),
                "null_violations_count": 0,
                "null_violations": [],
                "duplicate_count": 0,
                "missing_unique_key_count": 0,
            }

        null_violations: List[Dict[str, Any]] = []
        null_total = 0
        seen_keys = set()
        duplicate_count = 0
        missing_key_count = 0

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                null_total += 1
                if len(null_violations) < _MAX_LISTED:
                    null_violations.append({"row_index": idx, "field": None, "reason": "row_is_not_an_object"})
                continue

            for field in req:
                if field not in row or _is_missing(row.get(field)):
                    null_total += 1
                    if len(null_violations) < _MAX_LISTED:
                        null_violations.append({"row_index": idx, "field": field})

            if unique_key:
                key_val = row.get(unique_key)
                if _is_missing(key_val):
                    missing_key_count += 1
                    continue
                try:
                    hash(key_val)
                    hashable_key = key_val
                except TypeError:
                    hashable_key = repr(key_val)

                if hashable_key in seen_keys:
                    duplicate_count += 1
                else:
                    seen_keys.add(hashable_key)

        valid = null_total == 0 and duplicate_count == 0 and missing_key_count == 0
        return {
            "data_valid": valid,
            "rows_checked": len(rows),
            "null_violations_count": null_total,
            "null_violations": null_violations,
            "duplicate_count": duplicate_count,
            "missing_unique_key_count": missing_key_count,
        }

    @staticmethod
    def summarize_counts(
        null_counts: int = 0,
        duplicate_counts: int = 0,
        schema_errors: int = 0,
    ) -> Dict[str, Any]:
        """Summarize precomputed dataset quality counts into a validation decision."""
        valid = (null_counts == 0) and (duplicate_counts == 0) and (schema_errors == 0)
        return {
            "data_valid": valid,
            "null_counts": null_counts,
            "duplicate_counts": duplicate_counts,
            "schema_errors": schema_errors,
        }

    @staticmethod
    def check_numeric_bounds(
        records: List[Dict[str, Any]],
        field: str,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Check numeric values in a specified column against min/max bounds.

        NaN, infinite and non-numeric values are always violations. Bounds that are NaN/infinite or
        reversed, no rows, or a field that no row has are invalid input (``within_bounds`` False,
        ``error`` set) rather than a pass.
        """
        rows = _rows(records)
        invalid: Optional[str] = None
        for label, bound in (("min_value", min_value), ("max_value", max_value)):
            if bound is None:
                continue
            as_number = _as_float(bound)
            if as_number is None or not math.isfinite(as_number):
                invalid = f"{label} must be a finite number."
        if invalid is None and min_value is not None and max_value is not None and float(min_value) > float(max_value):
            invalid = "min_value is greater than max_value."
        if invalid is None and not rows:
            invalid = "No records were supplied, so nothing was checked."
        if invalid is not None:
            return {
                "field": field,
                "within_bounds": False,
                "error": invalid,
                "values_checked": 0,
                "violations_count": 0,
                "violations": [],
            }

        out_of_bounds: List[Dict[str, Any]] = []
        total = 0
        checked = 0
        lo = float(min_value) if min_value is not None else None
        hi = float(max_value) if max_value is not None else None

        def flag(entry: Dict[str, Any]) -> None:
            nonlocal total
            total += 1
            if len(out_of_bounds) < _MAX_LISTED:
                out_of_bounds.append(entry)

        for idx, row in enumerate(rows):
            if not isinstance(row, dict) or field not in row or row[field] is None:
                continue
            checked += 1
            raw_val = row[field]
            val = _as_float(raw_val)
            if val is None:
                flag({"row_index": idx, "value": raw_val, "reason": "non_numeric"})
            elif math.isnan(val):
                flag({"row_index": idx, "value": raw_val, "reason": "nan"})
            elif math.isinf(val):
                flag({"row_index": idx, "value": raw_val, "reason": "non_finite"})
            elif lo is not None and val < lo:
                flag({"row_index": idx, "value": val, "reason": f"< {min_value}"})
            elif hi is not None and val > hi:
                flag({"row_index": idx, "value": val, "reason": f"> {max_value}"})

        result: Dict[str, Any] = {
            "field": field,
            "within_bounds": total == 0 and checked > 0,
            "values_checked": checked,
            "violations_count": total,
            "violations": out_of_bounds,
        }
        if checked == 0:
            result["error"] = f"No row has a value for '{field}', so nothing was checked."
        return result

    @staticmethod
    def detect_missing_values(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate per-column missing and null value ratios (blank = None, NaN, "" or whitespace)."""
        rows = _rows(records)
        if not rows:
            return {"total_rows": 0, "columns": {}}

        dict_rows = [r for r in rows if isinstance(r, dict)]
        total_rows = len(rows)
        all_cols = set()
        for r in dict_rows:
            all_cols.update(r.keys())

        col_stats: Dict[str, Dict[str, Any]] = {}
        for col in sorted(all_cols, key=str):
            # A row that is not an object has no value for any column.
            missing_count = (total_rows - len(dict_rows)) + sum(1 for r in dict_rows if _is_missing(r.get(col)))
            col_stats[col] = {
                "missing_count": missing_count,
                "missing_ratio": round(missing_count / total_rows, 4),
            }

        result: Dict[str, Any] = {"total_rows": total_rows, "columns": col_stats}
        if len(dict_rows) != total_rows:
            result["non_object_rows"] = total_rows - len(dict_rows)
        return result

    @staticmethod
    def detect_duplicates(
        records: List[Dict[str, Any]],
        subset_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Find duplicate records based on full row or a subset of key columns."""
        rows = _rows(records) or []
        keys = [subset_keys] if isinstance(subset_keys, str) else subset_keys
        seen: Dict[Any, int] = {}
        duplicates: List[Dict[str, Any]] = []
        total = 0

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                key_tuple: Any = ("<not an object>", repr(row))
            elif keys:
                parts = []
                for k in keys:
                    v = row.get(k)
                    try:
                        hash(v)
                    except TypeError:
                        v = repr(v)
                    parts.append(v)
                key_tuple = tuple(parts)
            else:
                key_tuple = tuple(sorted(((str(k), repr(v)) for k, v in row.items())))

            if key_tuple in seen:
                total += 1
                if len(duplicates) < 50:
                    duplicates.append({
                        "original_row_index": seen[key_tuple],
                        "duplicate_row_index": idx,
                        "key": str(key_tuple),
                    })
            else:
                seen[key_tuple] = idx

        return {
            "has_duplicates": total > 0,
            "duplicates_count": total,
            "duplicates": duplicates,
        }

    @staticmethod
    def detect_statistical_outliers(
        records: List[Dict[str, Any]],
        field: str,
        z_score_threshold: float = 3.0,
    ) -> Dict[str, Any]:
        """Detect numeric outliers using the population z-score.

        Infinite values are outliers by definition (``z_score`` null, ``reason`` "non_finite") and
        are left out of the mean and deviation; NaN and non-numeric values are skipped and counted.
        With ``n`` values no z-score can exceed ``(n - 1) / sqrt(n)``: when the threshold is above
        that, ``note`` says no value could have been flagged, instead of implying the data is clean.
        """
        if (
            isinstance(z_score_threshold, bool)
            or not isinstance(z_score_threshold, (int, float))
            or not math.isfinite(z_score_threshold)
            or z_score_threshold <= 0
        ):
            return {
                "field": field,
                "outliers_count": 0,
                "outliers": [],
                "error": "z_score_threshold must be a finite number > 0.",
            }

        values: List[float] = []
        indices: List[int] = []
        infinite: List[Dict[str, Any]] = []
        skipped = 0

        for idx, row in enumerate(_rows(records) or []):
            val = row.get(field) if isinstance(row, dict) else None
            if val is None:
                continue
            f = _as_float(val)
            if f is None or math.isnan(f):
                skipped += 1
            elif math.isinf(f):
                infinite.append({"row_index": idx, "value": f, "z_score": None, "reason": "non_finite"})
            else:
                values.append(f)
                indices.append(idx)

        base: Dict[str, Any] = {"field": field, "skipped_non_numeric": skipped}
        if len(values) < 3:
            return {
                **base,
                "outliers_count": len(infinite),
                "outliers": infinite,
                "note": "Insufficient numeric sample size",
            }

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = math.sqrt(variance)

        if std_dev == 0.0:
            return {**base, "outliers_count": len(infinite), "outliers": infinite, "std_dev": 0.0}

        outliers: List[Dict[str, Any]] = list(infinite)
        for idx, val in zip(indices, values):
            z = abs(val - mean) / std_dev
            if z > z_score_threshold:
                outliers.append({
                    "row_index": idx,
                    "value": val,
                    "z_score": round(z, 2),
                })

        result = {
            **base,
            "mean": round(mean, 2),
            "std_dev": round(std_dev, 2),
            "outliers_count": len(outliers),
            "outliers": outliers,
        }
        max_possible_z = (len(values) - 1) / math.sqrt(len(values))
        if max_possible_z <= z_score_threshold:
            result["note"] = (
                f"With {len(values)} values no z-score can exceed {round(max_possible_z, 2)}, so a threshold "
                f"of {z_score_threshold} cannot flag anything; lower it or add data."
            )
        return result

    @staticmethod
    def validate_types_and_schemas(
        records: List[Dict[str, Any]],
        type_contracts: Dict[str, str],
    ) -> Dict[str, Any]:
        """Verify that column values match declared string, int, float, bool, list or dict types.

        ``None`` values are allowed (treated as absent). Booleans are not ints or floats, and
        NaN/infinity are not valid numbers. A type label this function does not know, no rows, no
        contracts, or a contracted column that no row has all make ``schema_compliant`` False
        (with ``error``), so a typo cannot pass.
        """
        type_map = {
            "str": str,
            "string": str,
            "int": int,
            "integer": int,
            "float": (int, float),
            "number": (int, float),
            "bool": bool,
            "boolean": bool,
            "dict": dict,
            "object": dict,
            "list": list,
            "array": list,
        }
        rows = _rows(records)
        contracts = type_contracts if isinstance(type_contracts, dict) else {}
        if not rows or not contracts:
            return {
                "schema_compliant": False,
                "error": (
                    "No records were supplied, so nothing was validated."
                    if not rows
                    else "No type_contracts were given, so nothing was validated."
                ),
                "violations_count": 0,
                "violations": [],
            }

        unknown_types = {
            str(col): str(label) for col, label in contracts.items() if str(label).strip().lower() not in type_map
        }
        violations: List[Dict[str, Any]] = []
        total = 0
        seen_columns = set()

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for col, label in contracts.items():
                exp_cls = type_map.get(str(label).strip().lower())
                if exp_cls is None or col not in row:
                    continue
                seen_columns.add(col)
                value = row[col]
                if value is None:
                    continue
                numeric = exp_cls in (int, (int, float))
                wrong = (
                    not isinstance(value, exp_cls)
                    or (numeric and isinstance(value, bool))
                    or (numeric and isinstance(value, float) and not math.isfinite(value))
                )
                if wrong:
                    total += 1
                    if len(violations) < 50:
                        violations.append({
                            "row_index": idx,
                            "column": col,
                            "expected_type": label,
                            "actual_type": type(value).__name__,
                            "value": repr(value)[:200],
                        })

        not_found = sorted(
            str(c) for c, label in contracts.items()
            if str(label).strip().lower() in type_map and c not in seen_columns
        )
        problems = []
        if unknown_types:
            problems.append("unknown type label(s) for: " + ", ".join(sorted(unknown_types)))
        if not_found:
            problems.append("no row has column(s): " + ", ".join(not_found))

        result: Dict[str, Any] = {
            "schema_compliant": total == 0 and not problems,
            "violations_count": total,
            "violations": violations,
        }
        if unknown_types:
            result["unknown_types"] = unknown_types
        if not_found:
            result["columns_not_found"] = not_found
        if problems:
            result["error"] = "; ".join(problems)
        return result
