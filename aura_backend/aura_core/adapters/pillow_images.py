"""Pillow implementation of the ``ImageNormalizer`` port (ADR-031).

Every image a user attaches passes through here before any model sees it. The contract is in
``ports/images.py``; this file is the how:

* the type is decided by magic bytes, then confirmed by what Pillow actually parsed -- a PNG
  named ``.jpg`` is a PNG, and an SVG or HTML file named ``.png`` is refused;
* the pixel count is read from the header and checked *before* ``load()``, so a 1 KB file that
  declares 50 000 x 50 000 pixels is refused without allocating them;
* EXIF orientation is applied, then every piece of metadata is dropped by re-encoding from a
  fresh info dict -- a phone photo's GPS position never leaves this function;
* only the first frame of an animation is kept;
* the long edge is shrunk to ``max_edge_px`` (never enlarged).

Screenshots stay PNG (text survives lossless better), images with transparency stay PNG, and
everything else becomes JPEG. Pillow is imported lazily, like every other vendor SDK.
"""

from __future__ import annotations

import asyncio
import io

from ..domain.errors import DependencyUnavailableError, ValidationError
from ..domain.images import ImageInput

__all__ = ["PillowImageNormalizer", "pillow_available", "sniff_image_type"]

_JPEG_QUALITY = 90


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def sniff_image_type(raw: bytes) -> str | None:
    """The media type ``raw`` really is, by its leading bytes, or ``None`` if not one we accept."""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


_PIL_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _refuse(reason: str, message: str, **details: object) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


class PillowImageNormalizer:
    def __init__(self, *, max_bytes: int, max_pixels: int, max_edge_px: int) -> None:
        if not pillow_available():
            raise DependencyUnavailableError(
                "PillowImageNormalizer requires Pillow", dependency="pillow"
            )
        import PIL

        self._name = f"pillow-{PIL.__version__}"
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._max_edge = max_edge_px

    @property
    def name(self) -> str:
        return self._name

    async def normalize(self, raw: bytes, *, declared_type: str | None = None) -> ImageInput:
        # ``declared_type`` is deliberately unused for the decision: a client's claim about its
        # own upload is not evidence. It is accepted so callers need not strip it.
        if not raw:
            raise _refuse("empty", "the image is empty")
        if len(raw) > self._max_bytes:
            raise _refuse("image_too_large", "the image exceeds the size limit",
                          limit_bytes=self._max_bytes)
        sniffed = sniff_image_type(raw)
        if sniffed is None:
            raise _refuse("unsupported_type", "only JPEG, PNG and WebP images are accepted")
        return await asyncio.to_thread(self._normalize, raw, sniffed)

    def _normalize(self, raw: bytes, sniffed: str) -> ImageInput:
        from PIL import Image, ImageOps

        try:
            with Image.open(io.BytesIO(raw), formats=[_PIL_FORMAT[sniffed]]) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0:
                    raise _refuse("undecodable", "the image has no pixels")
                if width * height > self._max_pixels:
                    raise _refuse("image_too_large", "the image has too many pixels",
                                  limit_pixels=self._max_pixels)
                opened.seek(0)
                opened.load()
                image = ImageOps.exif_transpose(opened) or opened.copy()
        except ValidationError:
            raise
        except Image.DecompressionBombError as exc:
            raise _refuse("image_too_large", "the image has too many pixels",
                          limit_pixels=self._max_pixels) from exc
        except Exception as exc:
            raise _refuse("undecodable", "the image could not be decoded",
                          error=type(exc).__name__) from exc

        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        keep_png = sniffed == "image/png" or has_alpha
        image = image.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((self._max_edge, self._max_edge), Image.Resampling.LANCZOS)
        # Re-encode from a clean info dict: nothing carried in from the upload (EXIF, XMP, ICC,
        # PNG text chunks, comments) reaches the encoder, because none of it is passed to save().
        image.info = {}
        out = io.BytesIO()
        if keep_png:
            image.save(out, format="PNG", optimize=True)
            media_type = "image/png"
        else:
            image.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            media_type = "image/jpeg"
        return ImageInput(
            media_type=media_type, data=out.getvalue(), width=image.width, height=image.height
        )
