"""ADR-031: every attached image is sniffed, bounded, stripped and shrunk before a model sees it.

Fixtures are generated in-test with Pillow, so the repository carries no binary image files and
each case states exactly what it feeds the normalizer.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from aura_core.adapters.pillow_images import PillowImageNormalizer, sniff_image_type  # noqa: E402
from aura_core.domain.errors import ValidationError  # noqa: E402
from aura_core.domain.images import ImageInput  # noqa: E402

GPS_IFD = 0x8825
ORIENTATION = 0x0112


def _normalizer(**overrides) -> PillowImageNormalizer:
    limits = {"max_bytes": 10 * 1024 * 1024, "max_pixels": 50_000_000, "max_edge_px": 1536}
    limits.update(overrides)
    return PillowImageNormalizer(**limits)


def _encode(image: Image.Image, fmt: str, **save) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **save)
    return out.getvalue()


def _jpeg_with_gps(size=(64, 48), orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[GPS_IFD] = {1: "N", 2: (30.0, 2.0, 41.0), 3: "E", 4: (31.0, 14.0, 9.0)}
    exif[0x010F] = "PhoneMaker"
    if orientation is not None:
        exif[ORIENTATION] = orientation
    return _encode(image, "JPEG", exif=exif.tobytes())


def _png_claiming(width: int, height: int) -> bytes:
    """A real 1x1 PNG whose IHDR is rewritten (with a valid CRC) to declare ``width x height``."""
    raw = bytearray(_encode(Image.new("RGB", (1, 1)), "PNG"))
    ihdr = raw.index(b"IHDR")
    body = struct.pack(">II", width, height) + bytes(raw[ihdr + 12 : ihdr + 17])
    raw[ihdr + 4 : ihdr + 17] = body
    raw[ihdr + 17 : ihdr + 21] = struct.pack(">I", zlib.crc32(b"IHDR" + body) & 0xFFFFFFFF)
    return bytes(raw)


def _reason(excinfo) -> str:
    return excinfo.value.details["reason"]


class TestSniffing:
    def test_types_are_decided_by_content(self) -> None:
        assert sniff_image_type(_encode(Image.new("RGB", (2, 2)), "PNG")) == "image/png"
        assert sniff_image_type(_encode(Image.new("RGB", (2, 2)), "JPEG")) == "image/jpeg"
        assert sniff_image_type(_encode(Image.new("RGB", (2, 2)), "WEBP")) == "image/webp"
        assert sniff_image_type(_encode(Image.new("RGB", (2, 2)), "GIF")) is None
        assert sniff_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None

    @pytest.mark.parametrize(
        "payload",
        [
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            b"<!doctype html><html><body>not a png</body></html>",
            b"GIF89a\x01\x00\x01\x00\x00\x00\x00;",
            b"%PDF-1.7\n",
        ],
    )
    async def test_anything_else_is_refused_whatever_the_client_claims(self, payload) -> None:
        with pytest.raises(ValidationError) as excinfo:
            await _normalizer().normalize(payload, declared_type="image/png")
        assert _reason(excinfo) == "unsupported_type"


class TestPrivacy:
    async def test_gps_and_every_other_exif_tag_are_gone(self) -> None:
        raw = _jpeg_with_gps()
        assert Image.open(io.BytesIO(raw)).getexif().get_ifd(GPS_IFD), "fixture must carry GPS"

        image = await _normalizer().normalize(raw)

        with Image.open(io.BytesIO(image.data)) as out:
            exif = out.getexif()
            assert not exif.get_ifd(GPS_IFD)
            assert len(exif) == 0
            assert "exif" not in out.info
        assert b"PhoneMaker" not in image.data

    async def test_png_text_chunks_are_dropped(self) -> None:
        from PIL import PngImagePlugin

        meta = PngImagePlugin.PngInfo()
        meta.add_text("Comment", "home address: 12 Example St")
        raw = _encode(Image.new("RGB", (8, 8)), "PNG", pnginfo=meta)

        image = await _normalizer().normalize(raw)

        assert b"Example St" not in image.data

    async def test_orientation_is_applied_before_the_tag_is_dropped(self) -> None:
        # Orientation 6 = rotate 90 CW on display: a 64x48 sensor image is shown 48x64.
        image = await _normalizer().normalize(_jpeg_with_gps(size=(64, 48), orientation=6))
        assert (image.width, image.height) == (48, 64)


class TestLimits:
    async def test_a_header_declaring_too_many_pixels_is_refused_without_decoding(self) -> None:
        bomb = _png_claiming(60_000, 60_000)
        assert len(bomb) < 200

        with pytest.raises(ValidationError) as excinfo:
            await _normalizer().normalize(bomb)
        assert _reason(excinfo) == "image_too_large"

    async def test_bytes_over_the_limit_are_refused_before_decoding(self) -> None:
        raw = _encode(Image.new("RGB", (64, 64)), "PNG")
        with pytest.raises(ValidationError) as excinfo:
            await _normalizer(max_bytes=len(raw) - 1).normalize(raw)
        assert _reason(excinfo) == "image_too_large"

    async def test_truncated_data_is_undecodable(self) -> None:
        raw = _encode(Image.new("RGB", (256, 256), (1, 2, 3)), "JPEG")
        with pytest.raises(ValidationError) as excinfo:
            await _normalizer().normalize(raw[: len(raw) // 3])
        assert _reason(excinfo) in {"undecodable"}

    async def test_the_long_edge_is_shrunk_and_never_enlarged(self) -> None:
        big = await _normalizer(max_edge_px=512).normalize(
            _encode(Image.new("RGB", (2048, 1024)), "JPEG")
        )
        assert (big.width, big.height) == (512, 256)

        small = await _normalizer(max_edge_px=512).normalize(
            _encode(Image.new("RGB", (100, 40)), "JPEG")
        )
        assert (small.width, small.height) == (100, 40)

    async def test_an_animation_keeps_its_first_frame_only(self) -> None:
        frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 255, 0))]
        out = io.BytesIO()
        frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:], lossless=True)

        image = await _normalizer().normalize(out.getvalue())

        with Image.open(io.BytesIO(image.data)) as decoded:
            assert getattr(decoded, "n_frames", 1) == 1
            assert decoded.convert("RGB").getpixel((16, 16))[0] > 200


class TestOutputFormat:
    async def test_screenshots_stay_png_and_photos_become_jpeg(self) -> None:
        png = await _normalizer().normalize(_encode(Image.new("RGB", (16, 16)), "PNG"))
        webp = await _normalizer().normalize(_encode(Image.new("RGB", (16, 16)), "WEBP"))
        assert png.media_type == "image/png"
        assert webp.media_type == "image/jpeg"

    async def test_transparency_is_kept(self) -> None:
        raw = _encode(Image.new("RGBA", (16, 16), (0, 0, 0, 0)), "WEBP", lossless=True)
        image = await _normalizer().normalize(raw)
        assert image.media_type == "image/png"
        with Image.open(io.BytesIO(image.data)) as decoded:
            assert decoded.mode == "RGBA"


class TestImageInput:
    def test_repr_and_describe_never_carry_the_bytes(self) -> None:
        data = b"\xff\xd8\xff" + b"SECRET-PIXELS" * 10
        image = ImageInput(media_type="image/jpeg", data=data, width=10, height=10)
        assert "SECRET" not in repr(image)
        assert "SECRET" not in str(image.describe())
        assert len(image.sha256) == 64

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"media_type": "image/svg+xml", "data": b"x", "width": 1, "height": 1},
            {"media_type": "image/png", "data": b"", "width": 1, "height": 1},
            {"media_type": "image/png", "data": b"x", "width": 0, "height": 1},
        ],
    )
    def test_invalid_images_cannot_be_constructed(self, kwargs) -> None:
        with pytest.raises(ValidationError):
            ImageInput(**kwargs)

    def test_token_estimate_grows_with_area(self) -> None:
        small = ImageInput(media_type="image/png", data=b"x", width=64, height=64)
        large = ImageInput(media_type="image/png", data=b"x", width=1536, height=1024)
        assert small.approx_tokens == 64
        assert large.approx_tokens == 48 * 32
