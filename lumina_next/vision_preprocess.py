from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from os import PathLike

try:
    from PIL import Image, ImageFile  # type: ignore
except Exception:  # pragma: no cover - Pillow is optional
    Image = None
    ImageFile = None  # type: ignore[assignment]


VISION_INPUT_MAX_BYTES = 4 * 1024 * 1024
VISION_INPUT_MAX_DIMENSION = 2048
VISION_INPUT_MAX_PIXELS = 4_194_304
VISION_INPUT_JPEG_QUALITY = 82

_MIME_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
_SUPPORTED_OUTPUT_MIME = frozenset({"image/jpeg", "image/png"})


class VisionPreprocessError(ValueError):
    """Raised when image input violates safety validation policy."""


@dataclass(frozen=True)
class VisionPreprocessResult:
    """Normalized image payload ready for vision model invocation."""

    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    normalized: bool
    original_bytes: int
    output_bytes: int


def _canonical_mime(value: str) -> str:
    value = value.strip().lower()
    if ";" in value:
        value = value.split(";", 1)[0].strip()
    if value == "image/jpg":
        return "image/jpeg"
    return value


def _ensure_bytes(payload: bytes | bytearray | memoryview) -> bytes:
    if isinstance(payload, memoryview):
        return payload.tobytes()
    return bytes(payload)


def _reject_pathlike(payload: object) -> None:
    if isinstance(payload, (Path, PathLike)):
        raise VisionPreprocessError("path-like input is not allowed")
    if isinstance(payload, str):
        raise VisionPreprocessError("path-like input is not allowed")


def _detect_format(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8"):
        return "jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "webp"
    raise VisionPreprocessError("unknown or invalid image signature")


def _parse_png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise VisionPreprocessError("invalid png header")
    if payload[12:16] != b"IHDR":
        raise VisionPreprocessError("png missing IHDR chunk")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width <= 0 or height <= 0:
        raise VisionPreprocessError("png dimensions are invalid")
    return width, height


def _parse_gif_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith((b"GIF87a", b"GIF89a")) or len(payload) < 10:
        raise VisionPreprocessError("invalid gif header")
    width = int.from_bytes(payload[6:8], "little")
    height = int.from_bytes(payload[8:10], "little")
    if width <= 0 or height <= 0:
        raise VisionPreprocessError("gif dimensions are invalid")
    return width, height


def _parse_jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 4 or not payload.startswith(b"\xff\xd8"):
        raise VisionPreprocessError("invalid jpeg header")

    i = 2
    end = len(payload)
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}

    while i < end - 1:
        if payload[i] != 0xFF:
            i += 1
            continue

        i += 1
        while i < end and payload[i] == 0xFF:
            i += 1
        if i >= end:
            break
        marker = payload[i]
        i += 1

        if marker in {0xD8, 0xD9}:
            continue
        if i + 1 >= end:
            break

        segment_length = int.from_bytes(payload[i : i + 2], "big")
        if segment_length < 2:
            raise VisionPreprocessError("invalid jpeg segment length")

        segment_start = i + 2
        segment_end = i + segment_length
        if segment_end > end:
            raise VisionPreprocessError("truncated jpeg segment")

        if marker in sof_markers:
            if segment_start + 7 > segment_end:
                raise VisionPreprocessError("invalid jpeg SOF segment")
            height = int.from_bytes(payload[segment_start + 1 : segment_start + 3], "big")
            width = int.from_bytes(payload[segment_start + 3 : segment_start + 5], "big")
            if width <= 0 or height <= 0:
                raise VisionPreprocessError("jpeg dimensions are invalid")
            return width, height

        i = segment_end

    raise VisionPreprocessError("jpeg has no SOF frame")


def _parse_webp_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(b"RIFF") or payload[8:12] != b"WEBP":
        raise VisionPreprocessError("invalid webp header")
    if len(payload) < 20:
        raise VisionPreprocessError("invalid webp payload")

    index = 12
    total = len(payload)
    while index + 8 <= total:
        tag = payload[index : index + 4]
        size = int.from_bytes(payload[index + 4 : index + 8], "little")
        data_start = index + 8
        data_end = data_start + size
        if data_end > total:
            raise VisionPreprocessError("invalid webp chunk size")

        data = payload[data_start:data_end]
        if tag == b"VP8X":
            if len(data) < 10:
                raise VisionPreprocessError("invalid webp VP8X chunk")
            width = 1 + (data[4] | (data[5] << 8) | (data[6] << 16))
            height = 1 + (data[7] | (data[8] << 8) | (data[9] << 16))
            if width <= 0 or height <= 0:
                raise VisionPreprocessError("webp dimensions are invalid")
            return width, height

        if tag == b"VP8L":
            if len(data) < 5:
                raise VisionPreprocessError("invalid webp VP8L chunk")
            width = 1 + (data[1] | ((data[2] & 0x3F) << 8))
            height = 1 + ((data[2] >> 6) | (data[3] << 2) | ((data[4] & 0x0F) << 10))
            if width <= 0 or height <= 0:
                raise VisionPreprocessError("webp dimensions are invalid")
            return width, height

        if tag == b"VP8 ":
            if len(data) < 10:
                raise VisionPreprocessError("invalid webp VP8 chunk")
            if data[3:6] != b"\x9d\x01\x2a":
                raise VisionPreprocessError("unsupported webp VP8 frame")
            width = 1 + (((data[7] << 8) | data[6]) & 0x3FFF)
            height = 1 + (((data[9] << 8) | data[8]) & 0x3FFF)
            if width <= 0 or height <= 0:
                raise VisionPreprocessError("webp dimensions are invalid")
            return width, height

        index = data_end + (size & 1)

    raise VisionPreprocessError("unsupported webp variant")


def _dimensions(payload: bytes, detected_format: str) -> tuple[int, int]:
    if detected_format == "png":
        return _parse_png_dimensions(payload)
    if detected_format == "jpeg":
        return _parse_jpeg_dimensions(payload)
    if detected_format == "gif":
        return _parse_gif_dimensions(payload)
    if detected_format == "webp":
        return _parse_webp_dimensions(payload)
    raise VisionPreprocessError(f"unsupported image format: {detected_format}")


def _normalize_image_with_pillow(
    payload: bytes,
    target_mime: str,
    max_dimension: int,
    jpeg_quality: int,
) -> bytes:
    if Image is None:
        raise VisionPreprocessError("pillow is required for normalization")
    if max_dimension < 1:
        raise VisionPreprocessError("max_dimension must be positive")
    if jpeg_quality < 1 or jpeg_quality > 100:
        raise VisionPreprocessError("jpeg_quality must be 1..100")

    ImageFile.LOAD_TRUNCATED_IMAGES = False  # type: ignore[attr-defined]
    Image.MAX_IMAGE_PIXELS = None  # policy check is done in _dimensions

    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.width > max_dimension or image.height > max_dimension:
                image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            if target_mime == "image/jpeg":
                if image.mode not in {"L", "RGB", "YCbCr"}:
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
            elif target_mime == "image/png":
                if image.mode == "CMYK":
                    image = image.convert("RGB")
                image.save(output, format="PNG", optimize=True)
            else:
                raise VisionPreprocessError(f"unsupported target format: {target_mime}")
            return output.getvalue()
    except Exception as exc:  # pragma: no cover - strict Pillow decode paths
        raise VisionPreprocessError(f"image decode or normalize failed: {exc}") from exc


def preprocess_vision_input(
    payload: bytes | bytearray | memoryview,
    *,
    expected_mime: str | None = None,
    max_bytes: int = VISION_INPUT_MAX_BYTES,
    max_dimension: int = VISION_INPUT_MAX_DIMENSION,
    max_pixels: int = VISION_INPUT_MAX_PIXELS,
    output_mime: str | None = None,
    jpeg_quality: int = VISION_INPUT_JPEG_QUALITY,
    normalize_if_needed: bool = True,
) -> VisionPreprocessResult:
    """Validate and normalize incoming image bytes for vision input."""

    _reject_pathlike(payload)
    data = _ensure_bytes(payload)

    if not isinstance(expected_mime, str) and expected_mime is not None:
        raise VisionPreprocessError("expected_mime must be a string")
    if max_bytes < 1:
        raise VisionPreprocessError("max_bytes must be positive")
    if max_dimension < 1:
        raise VisionPreprocessError("max_dimension must be at least 1")
    if max_pixels < 1:
        raise VisionPreprocessError("max_pixels must be at least 1")

    if len(data) == 0:
        raise VisionPreprocessError("empty image payload")
    if len(data) > max_bytes:
        raise VisionPreprocessError("image payload exceeds max_bytes")

    detected_format = _detect_format(data)
    mime_type = _MIME_BY_FORMAT[detected_format]

    if expected_mime is not None:
        declared_mime = _canonical_mime(expected_mime)
        if declared_mime != mime_type:
            raise VisionPreprocessError("MIME mismatch between declared and detected content")

    width, height = _dimensions(data, detected_format)
    pixels = width * height
    if pixels > max_pixels:
        raise VisionPreprocessError("image exceeds decompression-safe pixel budget")

    target_mime = _canonical_mime(output_mime) if output_mime is not None else mime_type
    if target_mime not in _SUPPORTED_OUTPUT_MIME:
        raise VisionPreprocessError(f"unsupported output_mime: {target_mime}")

    needs_resize = width > max_dimension or height > max_dimension
    needs_reencode = output_mime is not None and output_mime.lower() != mime_type
    should_normalize = normalize_if_needed and (needs_resize or needs_reencode)

    if not should_normalize:
        return VisionPreprocessResult(
            image_bytes=data,
            mime_type=mime_type,
            width=width,
            height=height,
            normalized=False,
            original_bytes=len(data),
            output_bytes=len(data),
        )

    normalized = _normalize_image_with_pillow(
        data,
        target_mime=target_mime,
        max_dimension=max_dimension,
        jpeg_quality=jpeg_quality,
    )
    normalized_width, normalized_height = _dimensions(normalized, _detect_format(normalized))

    return VisionPreprocessResult(
        image_bytes=normalized,
        mime_type=target_mime,
        width=normalized_width,
        height=normalized_height,
        normalized=True,
        original_bytes=len(data),
        output_bytes=len(normalized),
    )
