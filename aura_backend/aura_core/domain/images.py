"""An image a user attached to a turn (ADR-031).

By the time one of these exists the bytes have already been normalized by an
:class:`~aura_core.ports.images.ImageNormalizer`: the type was sniffed rather than taken from the
caller, the metadata (EXIF, GPS, XMP) is gone, and the pixels fit the configured caps. So the
domain can treat ``data`` as safe to hand a provider, and nothing here needs Pillow.

The bytes never belong in a log line, a trace, an audit row or an exception, so ``repr`` names
the image by its digest and size only.

Stdlib only (ADR-001).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from .errors import ValidationError

__all__ = ["ImageInput", "ALLOWED_IMAGE_TYPES", "IMAGE_TOKEN_PATCH_PX"]

#: The only types a normalizer may emit, and so the only types a provider is ever sent.
ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

#: Side of the square one vision token roughly covers. Qwen-VL-style encoders merge 2x2 patches
#: of 14-16 px; 32 px is a deliberately coarse, slightly pessimistic estimate used only to keep
#: the router's context-window check honest. It is not a billing number.
IMAGE_TOKEN_PATCH_PX = 32


@dataclass(frozen=True, slots=True)
class ImageInput:
    media_type: str
    data: bytes = field(repr=False)
    width: int
    height: int
    sha256: str = ""

    def __post_init__(self) -> None:
        if self.media_type not in ALLOWED_IMAGE_TYPES:
            raise ValidationError(
                "unsupported image type", details={"media_type": str(self.media_type)[:64]}
            )
        if not self.data:
            raise ValidationError("an image cannot be empty")
        if self.width <= 0 or self.height <= 0:
            raise ValidationError("an image must have positive dimensions")
        if not self.sha256:
            object.__setattr__(self, "sha256", hashlib.sha256(self.data).hexdigest())

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def approx_tokens(self) -> int:
        """Rough vision-token cost of this image (see :data:`IMAGE_TOKEN_PATCH_PX`)."""
        return max(64, math.ceil(self.width / IMAGE_TOKEN_PATCH_PX) * math.ceil(self.height / IMAGE_TOKEN_PATCH_PX))

    def describe(self) -> dict[str, object]:
        """What may be logged or returned about this image: never the bytes."""
        return {
            "sha256": self.sha256,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "bytes": self.size_bytes,
        }

    def __repr__(self) -> str:
        return (
            f"ImageInput({self.media_type}, {self.width}x{self.height}, "
            f"{self.size_bytes} bytes, sha256={self.sha256[:12]})"
        )
