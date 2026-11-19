import hashlib
from pathlib import Path
from typing import Any, Dict, Union


class UiVisualVerifier:
    """Verifier for comparing two UI screenshot buffers or files using hash and byte-diff ratio."""

    @staticmethod
    def _to_bytes(source: Union[str, Path, bytes, bytearray]) -> bytes:
        """Load bytes from raw bytes or an existing file path, falling back to UTF-8 text."""
        if isinstance(source, (bytes, bytearray)):
            return bytes(source)
        path = Path(str(source))
        if path.exists() and path.is_file():
            return path.read_bytes()
        return str(source).encode("utf-8")

    @classmethod
    def verify_visuals(
        cls,
        screenshot: Union[str, Path, bytes, bytearray],
        reference: Union[str, Path, bytes, bytearray],
        tolerance_percentage: float = 5.0,
        sample_limit: int = 4096,
    ) -> Dict[str, Any]:
        """Compare screenshot and reference bytes and decide if visual diff is within tolerance."""
        data_a = cls._to_bytes(screenshot)
        data_b = cls._to_bytes(reference)

        hash_a = hashlib.sha256(data_a).hexdigest()
        hash_b = hashlib.sha256(data_b).hexdigest()
        exact_match = hash_a == hash_b

        limit = max(1, sample_limit)
        sample_a = data_a[:limit]
        sample_b = data_b[:limit]
        denom = min(len(sample_a), len(sample_b))

        if denom == 0:
            diff_pct = 0.0 if len(data_a) == len(data_b) else 100.0
        else:
            mismatches = sum(1 for x, y in zip(sample_a, sample_b) if x != y)
            diff_pct = round((mismatches / denom) * 100.0, 2)

        passed = exact_match or diff_pct <= tolerance_percentage
        return {
            "visually_consistent": passed,
            "exact_match": exact_match,
            "diff_percentage": diff_pct,
            "tolerance_percentage": tolerance_percentage,
            "screenshot_sha256": hash_a,
            "reference_sha256": hash_b,
            "status": "PASSED_VISUAL_CHECK" if passed else "VISUAL_REGRESSION_DETECTED",
        }