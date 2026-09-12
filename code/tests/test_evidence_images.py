from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from buy_wait.evidence.images import resolve_image, validate_png
from buy_wait.evidence.schema import EvidenceError


def png(color="white"):
    out = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(out, format="PNG")
    return out.getvalue()


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "media" / "images"
        self.images.mkdir(parents=True)
        (self.images / "image_1.png").write_bytes(png())

    def test_exact_image_id_resolution_and_hash_change(self):
        first = resolve_image(self.root, "image_1")
        self.assertEqual((first.width, first.height), (8, 8))
        self.assertEqual(first.relative_path, "dataset/media/images/image_1.png")
        (self.images / "image_1.png").write_bytes(png("red"))
        self.assertNotEqual(first.sha256, resolve_image(self.root, "image_1").sha256)

    def test_missing_not_zero_or_invented_bytes(self):
        with self.assertRaisesRegex(EvidenceError, "missing_image"):
            resolve_image(self.root, "image_404")

    def test_path_and_extension_attacks_rejected(self):
        for value in ["../.env", "image_1.png", "https://example.com/image_1", "image_1/../image_2", "image_1\x00"]:
            with self.assertRaisesRegex(EvidenceError, "invalid_image_id"):
                resolve_image(self.root, value)
        external = self.root / "outside.png"
        external.write_bytes(png())
        (self.images / "image_2.png").symlink_to(external)
        with self.assertRaisesRegex(EvidenceError, "invalid_image_path"):
            resolve_image(self.root, "image_2")

    def test_invalid_container_and_pixels_fail(self):
        for content in [b"not PNG", b"\x89PNG\r\n\x1a\n", png()[:40]]:
            with self.assertRaises(EvidenceError):
                validate_png(content)
        with self.assertRaisesRegex(EvidenceError, "oversize_image"):
            validate_png(png(), max_pixels=1)
        with self.assertRaisesRegex(EvidenceError, "oversize_image"):
            validate_png(png(), max_bytes=1)


if __name__ == "__main__":
    unittest.main()
