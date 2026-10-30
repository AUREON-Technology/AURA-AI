"""Port for turning uploaded image bytes into a safe :class:`~aura_core.domain.images.ImageInput`.

One method, because the job is one decision: is this really an image we accept, and what is the
smallest faithful version of it to hand a model. Behind it is a decoder (Pillow today), which is
exactly the kind of vendor code ADR-001 keeps out of the domain.

Stdlib-only, like every other port (ADR-001).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.images import ImageInput

__all__ = ["ImageNormalizer"]


@runtime_checkable
class ImageNormalizer(Protocol):
    """Validate and normalize one untrusted image.

    An implementation must, in this order:

    * refuse anything whose *content* is not an allowed type -- the declared type and the file
      name are hints at best, so the bytes are sniffed;
    * refuse an image whose header declares more pixels than the configured cap, before
      decoding it (decompression bombs);
    * keep the first frame only;
    * drop every piece of metadata, EXIF and GPS included, by re-encoding rather than editing;
    * downscale so the long edge fits the configured cap, never upscale.

    Raises :class:`~aura_core.domain.errors.ValidationError` for anything refused, with a
    ``details["reason"]`` a client can act on. Async because decoding is CPU-bound and must run
    off the event loop (``test_ports_are_async``).
    """

    @property
    def name(self) -> str:
        """Identifier for logs and traces -- e.g. ``"pillow-13.0.0"``."""
        ...

    async def normalize(self, raw: bytes, *, declared_type: str | None = None) -> ImageInput:
        ...
