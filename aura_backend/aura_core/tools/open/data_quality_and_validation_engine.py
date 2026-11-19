import math
from typing import Any, Dict, List, Optional


class DataQualityAndValidationEngine:
    """Engine for checking null counts, duplicate records, schema keys, and value ranges on tabular rows."""

    @staticmethod
    def validate_records(
        records: List[Dict[str, Any]],
        required_fields: Optional[List[str]] = None,
        unique_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate a list of row dicts for missing/null required fields and duplicate unique_key values."""
        req = required_fields or []
        null_violations: List[Dict[str, Any]] = []
        seen_keys = set()
        duplicate_count = 0

        for idx, row in enumerate(records):
            for field in req:
                val = row.get(field)
                is_nan = isinstance(val, float) and math.isnan(val)
                if field not in row or val is None or val == "" or is_nan:
                    null_violations.append({"row_index": idx, "field": field})

            if unique_key and unique_key in row and row[unique_key] is not None:
                key_val = row[unique_key]
                try:
                    hashable_key = key_val
                    hash(hashable_key)
                except TypeError:
                    hashable_key = repr(key_val)

                if hashable_key in seen_keys:
                    duplicate_count += 1
                else:
                    seen_keys.add(hashable_key)

        valid = len(null_violations) == 0 and duplicate_count == 0
        return {
            "data_valid": valid,
            "rows_checked": len(records),
            "null_violations_count": len(null_violations),
            "null_violations": null_violations,
            "duplicate_count": duplicate_count,
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
        """Check numeric values in a specified column against min/max bounds."""
        out_of_bounds: List[Dict[str, Any]] = []
        for idx, row in enumerate(records):
            if field not in row or row[field] is None:
                continue
            raw_val = row[field]
            if isinstance(raw_val, bool):
                out_of_bounds.append({"row_index": idx, "value": raw_val, "reason": "non_numeric"})
                continue
            try:
                val = float(raw_val)
            except (TypeError, ValueError):
                out_of_bounds.append({"row_index": idx, "value": raw_val, "reason": "non_numeric"})
                continue

            if math.isnan(val):
                out_of_bounds.append({"row_index": idx, "value": raw_val, "reason": "nan"})
            elif min_value is not None and val < min_value:
                out_of_bounds.append({"row_index": idx, "value": val, "reason": f"< {min_value}"})
            elif max_value is not None and val > max_value:
                out_of_bounds.append({"row_index": idx, "value": val, "reason": f"> {max_value}"})

        return {
            "field": field,
            "within_bounds": len(out_of_bounds) == 0,
            "violations_count": len(out_of_bounds),
            "violations": out_of_bounds,
        }