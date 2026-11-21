"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. ``_to_bytes`` reads any
existing path on the gateway, image bytes do not travel well as tool JSON, and the "perceptual"
hash is over raw (compressed) bytes.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import hashlib
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class UiVisualVerifier:
    """Verifier for comparing UI screenshots, header dimensions, perceptual similarity, and visual regressions."""

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

    @classmethod
    def extract_image_dimensions(cls, image_data: Union[str, Path, bytes, bytearray]) -> Optional[Tuple[int, int, str]]:
        """Extract (width, height, format) directly from image headers (PNG, GIF, JPEG)."""
        data = cls._to_bytes(image_data)
        if len(data) < 24:
            return None

        # PNG Header
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return (w, h, "PNG")

        # GIF Header
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return (w, h, "GIF")

        # Basic JPEG SOI scan
        if data[:2] == b"\xff\xd8":
            idx = 2
            while idx < len(data) - 9:
                marker, length = struct.unpack(">2sH", data[idx : idx + 4])
                if marker in (b"\xff\xc0", b"\xff\xc2"):  # SOF0, SOF2
                    h, w = struct.unpack(">HH", data[idx + 5 : idx + 9])
                    return (w, h, "JPEG")
                idx += 2 + length

        return None

    @classmethod
    def compare_image_dimensions(
        cls,
        image_a: Union[str, Path, bytes],
        image_b: Union[str, Path, bytes],
    ) -> Dict[str, Any]:
        """Compare width, height, and format of two images."""
        dim_a = cls.extract_image_dimensions(image_a)
        dim_b = cls.extract_image_dimensions(image_b)

        if not dim_a or not dim_b:
            return {
                "match": False,
                "error": "Could not parse image header dimensions for one or both images.",
                "dim_a": dim_a,
                "dim_b": dim_b,
            }

        w_a, h_a, fmt_a = dim_a
        w_b, h_b, fmt_b = dim_b
        dimensions_match = (w_a == w_b) and (h_a == h_b)

        return {
            "match": dimensions_match,
            "screenshot": {"width": w_a, "height": h_a, "format": fmt_a},
            "reference": {"width": w_b, "height": h_b, "format": fmt_b},
            "ratio_difference": round(abs((w_a * h_a) - (w_b * h_b)) / max(1, w_b * h_b) * 100.0, 2),
        }

    @classmethod
    def compute_perceptual_hash(cls, source: Union[str, Path, bytes]) -> str:
        """Compute a block-based luminance fingerprint from byte stream for visual similarity comparisons."""
        data = cls._to_bytes(source)
        if not data:
            return "0" * 16

        # Sample 64 blocks across data stream
        chunk_size = max(1, len(data) // 64)
        block_means: List[int] = []
        for i in range(64):
            chunk = data[i * chunk_size : (i + 1) * chunk_size]
            mean_val = sum(chunk) // len(chunk) if chunk else 0
            block_means.append(mean_val)

        overall_mean = sum(block_means) // len(block_means)
        bits = "".join("1" if b >= overall_mean else "0" for b in block_means)
        # Convert 64 bits to 16 hex digits
        return f"{int(bits, 2):016x}"

    @classmethod
    def calculate_pixel_difference(
        cls,
        screenshot: Union[str, Path, bytes],
        reference: Union[str, Path, bytes],
        threshold: float = 0.05,
    ) -> Dict[str, Any]:
        """Perform comprehensive visual comparison combining exact sha256, perceptual hash, and dimension check."""
        base_res = cls.verify_visuals(screenshot, reference, tolerance_percentage=threshold * 100.0)
        dim_res = cls.compare_image_dimensions(screenshot, reference)

        phash_a = cls.compute_perceptual_hash(screenshot)
        phash_b = cls.compute_perceptual_hash(reference)

        # Hamming distance between perceptual hashes
        int_a = int(phash_a, 16)
        int_b = int(phash_b, 16)
        hamming_dist = bin(int_a ^ int_b).count("1")

        is_similar = hamming_dist <= 10 and base_res["visually_consistent"]

        return {
            "is_visually_matching": is_similar,
            "hamming_distance": hamming_dist,
            "max_tolerated_distance": 10,
            "dimensions_aligned": dim_res.get("match", True),
            "exact_sha256_match": base_res["exact_match"],
            "diff_percentage": base_res["diff_percentage"],
            "phash_screenshot": phash_a,
            "phash_reference": phash_b,
        }
