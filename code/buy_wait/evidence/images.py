"""Resolve only supplied PNG IDs inside dataset/media/images."""
from __future__ import annotations

import hashlib
import io
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .schema import EvidenceError


@dataclass(frozen=True)
class ImageAsset:
    image_id: str
    relative_path: str
    sha256: str
    width: int
    height: int
    content: bytes = field(repr=False)


def validate_png(content: bytes, *, max_bytes: int = 10 * 1024 * 1024,
                 max_pixels: int = 25_000_000) -> tuple[int, int]:
    if len(content) > max_bytes:
        raise EvidenceError("oversize_image")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise EvidenceError("invalid_png")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as im:
                if im.format != "PNG" or im.width * im.height > max_pixels or im.width <= 0 or im.height <= 0:
                    raise EvidenceError("oversize_image")
                dimensions = im.size
                im.verify()
            with Image.open(io.BytesIO(content)) as im:
                im.load()  # verify compressed pixels, not just the container header
            return dimensions
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("invalid_png") from None


def resolve_image(dataset_root: Path, image_id: str) -> ImageAsset:
    if not isinstance(image_id, str) or re.fullmatch(r"image_[0-9]+", image_id) is None:
        raise EvidenceError("invalid_image_id")
    root = Path(dataset_root).resolve()
    directory = root / "media" / "images"
    # Even a symlinked image directory may not escape the declared dataset.
    try:
        directory.resolve().relative_to(root)
        path = directory / (image_id + ".png")
        resolved = path.resolve()
        resolved.relative_to(directory.resolve())
        if not resolved.is_file():
            raise EvidenceError("missing_image")
        if resolved.stat().st_size > 10 * 1024 * 1024:
            raise EvidenceError("oversize_image")
        content = resolved.read_bytes()
    except EvidenceError:
        raise
    except (ValueError, OSError, RuntimeError):
        raise EvidenceError("invalid_image_path") from None
    width, height = validate_png(content)
    return ImageAsset(image_id, f"media/images/{image_id}.png",
                      hashlib.sha256(content).hexdigest(), width, height, content)
