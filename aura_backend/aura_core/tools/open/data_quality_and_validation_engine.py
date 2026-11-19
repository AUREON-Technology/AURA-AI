import math
from typing import Any, Dict, List, Optional


class DataQualityAndValidationEngine:
    """Engine for checking null counts, duplicate records, schema types, outlier detection, and numeric bounds."""

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

    @staticmethod
    def detect_missing_values(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate per-column missing and null value ratios."""
        if not records:
            return {"total_rows": 0, "columns": {}}

        total_rows = len(records)
        all_cols = set()
        for r in records:
            all_cols.update(r.keys())

        col_stats: Dict[str, Dict[str, Any]] = {}
        for col in sorted(all_cols):
            missing_count = 0
            for r in records:
                v = r.get(col)
                if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
                    missing_count += 1
            col_stats[col] = {
                "missing_count": missing_count,
                "missing_ratio": round(missing_count / total_rows, 4),
            }

        return {"total_rows": total_rows, "columns": col_stats}

    @staticmethod
    def detect_duplicates(
        records: List[Dict[str, Any]],
        subset_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Find duplicate records based on full row or a subset of key columns."""
        seen: Dict[Any, int] = {}
        duplicates: List[Dict[str, Any]] = []

        for idx, row in enumerate(records):
            if subset_keys:
                key_tuple = tuple(row.get(k) for k in subset_keys)
            else:
                key_tuple = tuple(sorted((k, repr(v)) for k, v in row.items()))

            if key_tuple in seen:
                duplicates.append({
                    "original_row_index": seen[key_tuple],
                    "duplicate_row_index": idx,
                    "key": str(key_tuple),
                })
            else:
                seen[key_tuple] = idx

        return {
            "has_duplicates": len(duplicates) > 0,
            "duplicates_count": len(duplicates),
            "duplicates": duplicates[:50],
        }

    @staticmethod
    def detect_statistical_outliers(
        records: List[Dict[str, Any]],
        field: str,
        z_score_threshold: float = 3.0,
    ) -> Dict[str, Any]:
        """Detect numeric outliers using standard z-score metric."""
        values: List[float] = []
        indices: List[int] = []

        for idx, row in enumerate(records):
            val = row.get(field)
            if val is not None and not isinstance(val, bool):
                try:
                    f = float(val)
                    if not math.isnan(f):
                        values.append(f)
                        indices.append(idx)
                except (ValueError, TypeError):
                    pass

        if len(values) < 3:
            return {"field": field, "outliers_count": 0, "outliers": [], "note": "Insufficient numeric sample size"}

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = math.sqrt(variance)

        if std_dev == 0.0:
            return {"field": field, "outliers_count": 0, "outliers": [], "std_dev": 0.0}

        outliers: List[Dict[str, Any]] = []
        for idx, val in zip(indices, values):
            z = abs(val - mean) / std_dev
            if z > z_score_threshold:
                outliers.append({
                    "row_index": idx,
                    "value": val,
                    "z_score": round(z, 2),
                })

        return {
            "field": field,
            "mean": round(mean, 2),
            "std_dev": round(std_dev, 2),
            "outliers_count": len(outliers),
            "outliers": outliers,
        }

    @staticmethod
    def validate_types_and_schemas(
        records: List[Dict[str, Any]],
        type_contracts: Dict[str, str],
    ) -> Dict[str, Any]:
        """Verify that column values match declared string, int, float, bool, or dict types."""
        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "dict": dict,
            "list": list,
        }
        violations: List[Dict[str, Any]] = []

        for idx, row in enumerate(records):
            for col, expected_type_str in type_contracts.items():
                if col in row and row[col] is not None:
                    exp_cls = type_map.get(expected_type_str.lower())
                    if exp_cls and not isinstance(row[col], exp_cls):
                        violations.append({
                            "row_index": idx,
                            "column": col,
                            "expected_type": expected_type_str,
                            "actual_type": type(row[col]).__name__,
                            "value": repr(row[col]),
                        })

        return {
            "schema_compliant": len(violations) == 0,
            "violations_count": len(violations),
            "violations": violations[:50],
        }
