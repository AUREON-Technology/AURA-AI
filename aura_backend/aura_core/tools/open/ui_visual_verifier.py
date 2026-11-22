"""Byte-level comparison of screenshots and reference images (and of PDF documents).

This is a *heuristic*. It compares encoded bytes, not decoded pixels: a re-encoded but visually
identical PNG differs almost everywhere, and two different pictures can share most of their bytes.
Every result therefore names its ``method`` and never offers a pass it did not earn:

* the whole input is compared (the previous default looked only at the first 4096 bytes, so two
  screenshots that differed after that point were reported as matching);
* a length difference counts as a difference;
* if the two images' sizes differ, or a header cannot be parsed, only an exact byte match passes;
* a **PDF** is read in full through ``_input`` (every page) and compared by its extracted text; a
  page that is a scanned image cannot be compared, so the result is then not a pass unless the files
  are byte-identical.

A string argument is a *path*. It is never silently reinterpreted as the image's text: a path that
does not exist is an error (previously it was hashed as UTF-8 text, so two identical missing paths
"matched"). ``base_dir`` confines paths.
"""

import hashlib
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ._input import MAX_PDF_BYTES, SourceReadError, is_pdf_bytes, read_source_bytes

#: Larger than any screenshot; a bigger "image" is refused rather than read partially.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

Source = Union[str, Path, bytes, bytearray]


class VisualInputError(ValueError):
    """An image/PDF argument could not be loaded. ``str(exc)`` is safe to show the user."""


_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class UiVisualVerifier:
    """Verifier for comparing UI screenshots, header dimensions, perceptual similarity, and visual regressions."""

    @staticmethod
    def _to_bytes(source: Source, base_dir: Union[str, Path, None] = None) -> bytes:
        """Load bytes from raw bytes or an existing file path. Raises :class:`VisualInputError`."""
        if isinstance(source, (bytes, bytearray)):
            return bytes(source)
        if source is None or not isinstance(source, (str, Path)):
            raise VisualInputError(f"Expected image bytes or a file path, got {type(source).__name__}.")
        try:
            path = Path(str(source)).expanduser()
            resolved = path.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise VisualInputError(f"Invalid path: {source}") from exc
        if base_dir is not None:
            try:
                resolved.relative_to(Path(base_dir).resolve())
            except ValueError:
                raise VisualInputError(f"Path is outside the allowed directory: {source}") from None
        if not resolved.exists():
            raise VisualInputError(f"File not found: {source}")
        if not resolved.is_file():
            raise VisualInputError(f"Path is not a file: {source}")
        try:
            size = resolved.stat().st_size
            limit = MAX_PDF_BYTES if resolved.suffix.lower() == ".pdf" else MAX_IMAGE_BYTES
            if size > limit:
                raise VisualInputError(f"File is too large to compare safely ({size} bytes; limit {limit}): {source}")
            return resolved.read_bytes()
        except VisualInputError:
            raise
        except OSError as exc:
            raise VisualInputError(f"Failed to read file: {exc.strerror or type(exc).__name__}") from exc

    @classmethod
    def _load_pair(cls, a: Source, b: Source, base_dir: Any) -> Tuple[bytes, bytes]:
        return cls._to_bytes(a, base_dir), cls._to_bytes(b, base_dir)

    @staticmethod
    def _mismatch_count(a: bytes, b: bytes) -> int:
        """Differing byte positions over the whole of both inputs; the length difference counts too."""
        common = min(len(a), len(b))
        mism = max(len(a), len(b)) - common
        step = 65536
        for off in range(0, common, step):
            ca, cb = a[off : off + step], b[off : off + step]
            if ca != cb:
                mism += sum(1 for x, y in zip(ca, cb) if x != y)
        return mism

    @classmethod
    def _compare_pdfs(cls, data_a: bytes, data_b: bytes, name_a: str, name_b: str) -> Dict[str, Any]:
        """Compare two PDFs by the text of every page. Raises :class:`VisualInputError` if unreadable."""
        try:
            ra, rb = read_source_bytes(data_a, name=name_a), read_source_bytes(data_b, name=name_b)
        except SourceReadError as exc:
            raise VisualInputError(str(exc)) from exc
        lines_a, lines_b = Counter(ra.text.splitlines()), Counter(rb.text.splitlines())
        total = max(sum(lines_a.values()), sum(lines_b.values()))
        shared = sum((lines_a & lines_b).values())
        diff_pct = 0.0 if total == 0 else round((1 - shared / total) * 100.0, 2)
        return {
            "diff_percentage": diff_pct,
            "complete": ra.complete and rb.complete and ra.pages == rb.pages,
            "screenshot_input": ra.describe(),
            "reference_input": rb.describe(),
            "warnings": ra.warnings() + rb.warnings(),
            "pages_equal": ra.pages == rb.pages,
        }

    @classmethod
    def verify_visuals(
        cls,
        screenshot: Source,
        reference: Source,
        tolerance_percentage: float = 5.0,
        sample_limit: Optional[int] = None,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Compare screenshot and reference bytes and decide if visual diff is within tolerance.

        The whole of both inputs is compared. ``sample_limit`` (bytes) restricts the comparison to a
        prefix; a prefix-only comparison can prove a difference but never a match, so it passes only
        on an exact full-file match.
        """
        try:
            tol = float(tolerance_percentage)
            if tol != tol or tol < 0:
                raise ValueError
        except (TypeError, ValueError):
            return cls._error_result(f"tolerance_percentage must be a non-negative number, got {tolerance_percentage!r}.")
        try:
            data_a, data_b = cls._load_pair(screenshot, reference, base_dir)
        except VisualInputError as exc:
            return cls._error_result(str(exc), tol)

        hash_a = hashlib.sha256(data_a).hexdigest()
        hash_b = hashlib.sha256(data_b).hexdigest()
        exact_match = hash_a == hash_b
        result: Dict[str, Any] = {
            "tolerance_percentage": tol,
            "screenshot_sha256": hash_a,
            "reference_sha256": hash_b,
            "exact_match": exact_match,
        }
        warnings: List[str] = []

        pdf_a, pdf_b = is_pdf_bytes(data_a), is_pdf_bytes(data_b)
        if pdf_a != pdf_b:
            return cls._error_result("One input is a PDF and the other is not; they cannot be compared.", tol, result)
        if pdf_a and pdf_b:
            try:
                cmp = cls._compare_pdfs(data_a, data_b, "screenshot", "reference")
            except VisualInputError as exc:
                return cls._error_result(str(exc), tol, result)
            diff_pct = cmp["diff_percentage"]
            passed = exact_match or (cmp["complete"] and diff_pct <= tol)
            warnings.extend(cmp["warnings"])
            if not cmp["pages_equal"]:
                warnings.append("The two PDFs have a different number of pages.")
            if not exact_match and not cmp["complete"]:
                warnings.append("Not every page could be compared, so no match is claimed.")
            result.update({
                "method": "pdf_text_comparison",
                "diff_percentage": diff_pct,
                "complete": exact_match or cmp["complete"],
                "screenshot_input": cmp["screenshot_input"],
                "reference_input": cmp["reference_input"],
            })
        else:
            sample = None
            if sample_limit is not None:
                try:
                    sample = max(1, int(sample_limit))
                except (TypeError, ValueError, OverflowError):
                    return cls._error_result(f"sample_limit must be a positive integer, got {sample_limit!r}.", tol, result)
            if sample is not None and sample < max(len(data_a), len(data_b)):
                pa, pb = data_a[:sample], data_b[:sample]
                denom = max(len(pa), len(pb))
                complete = False
                warnings.append(
                    f"Only the first {sample} bytes were compared (sample_limit); a match cannot be claimed."
                )
            else:
                pa, pb = data_a, data_b
                denom = max(len(pa), len(pb))
                complete = True
            diff_pct = 0.0 if denom == 0 else round(cls._mismatch_count(pa, pb) / denom * 100.0, 2)
            passed = exact_match or (complete and diff_pct <= tol)
            result.update({
                "method": "byte_comparison",
                "diff_percentage": diff_pct,
                "complete": complete,
                "bytes_compared": denom,
            })
            if not exact_match:
                warnings.append(
                    "Encoded bytes are compared, not decoded pixels; use a pixel-diff tool for a visual verdict."
                )

        result.update({
            "visually_consistent": bool(passed),
            "status": "PASSED_VISUAL_CHECK" if passed else "VISUAL_REGRESSION_DETECTED",
        })
        if warnings:
            result["warnings"] = warnings
        return result

    @staticmethod
    def _error_result(message: str, tol: Optional[float] = None, partial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(partial or {})
        out.update({
            "visually_consistent": False,
            "exact_match": False,
            "diff_percentage": None,
            "tolerance_percentage": tol,
            "status": "INPUT_ERROR",
            "error": message,
        })
        return out

    @classmethod
    def extract_image_dimensions(
        cls, image_data: Source, base_dir: Union[str, Path, None] = None
    ) -> Optional[Tuple[int, int, str]]:
        """Extract (width, height, format) directly from image headers (PNG, GIF, JPEG).

        ``None`` when the data is not a PNG/GIF/JPEG (a PDF is not an image) or cannot be loaded.
        """
        try:
            data = cls._to_bytes(image_data, base_dir)
        except VisualInputError:
            return None
        return cls._dimensions_of(data)

    @staticmethod
    def _dimensions_of(data: bytes) -> Optional[Tuple[int, int, str]]:
        if len(data) < 10:
            return None

        # PNG Header
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return (w, h, "PNG")

        # GIF Header
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return (w, h, "GIF")

        # JPEG: walk the marker segments up to a start-of-frame segment.
        if data[:2] == b"\xff\xd8":
            idx = 2
            n = len(data)
            while idx + 4 <= n:
                if data[idx] != 0xFF:
                    return None  # corrupt stream
                while idx < n and data[idx] == 0xFF:  # fill bytes
                    idx += 1
                if idx >= n:
                    return None
                marker = data[idx]
                idx += 1
                if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                    continue  # standalone marker, no length
                if marker == 0xD9 or marker == 0xDA:
                    return None  # end of image / start of scan before any frame header
                if idx + 2 > n:
                    return None
                length = struct.unpack(">H", data[idx : idx + 2])[0]
                if marker in _SOF_MARKERS:
                    if idx + 7 > n:
                        return None
                    h, w = struct.unpack(">HH", data[idx + 3 : idx + 7])
                    return (w, h, "JPEG")
                if length < 2:
                    return None
                idx += length

        return None

    @classmethod
    def compare_image_dimensions(
        cls,
        image_a: Source,
        image_b: Source,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Compare width, height, and format of two images."""
        try:
            data_a, data_b = cls._load_pair(image_a, image_b, base_dir)
        except VisualInputError as exc:
            return {"match": False, "error": str(exc), "dim_a": None, "dim_b": None}
        dim_a = cls._dimensions_of(data_a)
        dim_b = cls._dimensions_of(data_b)

        if not dim_a or not dim_b:
            why = ""
            for label, data in (("first", data_a), ("second", data_b)):
                if is_pdf_bytes(data):
                    why += f" The {label} input is a PDF, which is not a PNG/GIF/JPEG image."
            return {
                "match": False,
                "error": "Could not parse image header dimensions for one or both images." + why,
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
    def compute_perceptual_hash(cls, source: Source, base_dir: Union[str, Path, None] = None) -> str:
        """Compute a block-based fingerprint of the byte stream (64 blocks -> 16 hex digits).

        This is a fingerprint of the *encoded bytes*, useful for spotting near-identical files, not a
        true perceptual hash of the picture. Every byte contributes (the last block runs to the end).
        Raises :class:`VisualInputError` if ``source`` cannot be loaded.
        """
        data = cls._to_bytes(source, base_dir)
        if not data:
            return "0" * 16

        chunk_size = max(1, len(data) // 64)
        block_means: List[int] = []
        for i in range(64):
            end = len(data) if i == 63 and len(data) >= 64 else (i + 1) * chunk_size
            chunk = data[i * chunk_size : end]
            block_means.append(sum(chunk) // len(chunk) if chunk else 0)

        overall_mean = sum(block_means) // len(block_means)
        bits = "".join("1" if b >= overall_mean else "0" for b in block_means)
        return f"{int(bits, 2):016x}"

    @classmethod
    def calculate_pixel_difference(
        cls,
        screenshot: Source,
        reference: Source,
        threshold: float = 0.05,
        base_dir: Union[str, Path, None] = None,
    ) -> Dict[str, Any]:
        """Combine exact sha256, a byte fingerprint, and a dimension check.

        ``is_visually_matching`` is true for a byte-identical pair, or when both headers parse, the
        dimensions agree, the fingerprints are close and the full-byte difference is within
        ``threshold``. If a header cannot be parsed (or sizes differ) only an exact match passes.
        """
        try:
            thr = float(threshold)
            if thr != thr or thr < 0:
                raise ValueError
        except (TypeError, ValueError):
            return {"is_visually_matching": False, "status": "INPUT_ERROR",
                    "error": f"threshold must be a non-negative number, got {threshold!r}."}
        base_res = cls.verify_visuals(screenshot, reference, tolerance_percentage=thr * 100.0, base_dir=base_dir)
        if base_res.get("status") == "INPUT_ERROR":
            return {"is_visually_matching": False, "status": "INPUT_ERROR", "error": base_res["error"]}
        dim_res = cls.compare_image_dimensions(screenshot, reference, base_dir=base_dir)
        dims_known = "error" not in dim_res
        aligned = bool(dim_res.get("match", False))

        if base_res["method"] == "pdf_text_comparison":
            phash_a = phash_b = None
            hamming_dist = 0
            is_similar = bool(base_res["visually_consistent"])
        else:
            phash_a = cls.compute_perceptual_hash(screenshot, base_dir)
            phash_b = cls.compute_perceptual_hash(reference, base_dir)
            hamming_dist = bin(int(phash_a, 16) ^ int(phash_b, 16)).count("1")
            fuzzy_ok = dims_known and aligned and hamming_dist <= 10 and base_res["visually_consistent"]
            is_similar = bool(base_res["exact_match"] or fuzzy_ok)

        out: Dict[str, Any] = {
            "is_visually_matching": is_similar,
            "hamming_distance": hamming_dist,
            "max_tolerated_distance": 10,
            "dimensions_aligned": aligned if dims_known else None,
            "exact_sha256_match": base_res["exact_match"],
            "diff_percentage": base_res["diff_percentage"],
            "phash_screenshot": phash_a,
            "phash_reference": phash_b,
        }
        if not dims_known and base_res["method"] != "pdf_text_comparison":
            out["warnings"] = ["Image dimensions could not be read, so only an exact byte match is accepted."]
        if "warnings" in base_res:
            out["warnings"] = out.get("warnings", []) + base_res["warnings"]
        return out
