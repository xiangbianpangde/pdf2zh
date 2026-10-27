"""Image inspection and normalization: content decides format, not the suffix."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from errors import ImageError
import images


class CharacterisationTests(unittest.TestCase):
    def test_png_bytes_named_jpg_are_recognised_as_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "figure.jpg"
            path.write_bytes(support.png_bytes(12, 9))
            info = images.inspect(path)
            self.assertEqual((info.mime, info.suffix), ("image/png", ".png"))
            self.assertEqual((info.width, info.height), (12, 9))

    def test_non_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "figure.png"
            path.write_text("not an image", encoding="utf-8")
            with self.assertRaises(ImageError):
                images.inspect(path)

    def test_active_svg_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagram.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>',
                            encoding="utf-8")
            with self.assertRaises(ImageError):
                images.inspect(path)

    def test_plain_svg_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagram.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8"/>',
                            encoding="utf-8")
            info = images.inspect(path)
            self.assertEqual(info.mime, "image/svg+xml")


class NormalizeTests(unittest.TestCase):
    def test_suffix_is_corrected_and_entry_points_at_images_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "figure.jpg"
            source.write_bytes(support.png_bytes(20, 20))
            target, entry = images.normalize(source, root / "images")
            self.assertEqual(target.name, "figure.png")
            self.assertEqual(entry["output"], "images/figure.png")
            self.assertEqual(entry["mime"], "image/png")

    def test_identical_bytes_are_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = support.png_bytes(4, 4)
            first = root / "a.png"
            second = root / "a.png"  # same name, same bytes
            first.write_bytes(payload)
            target_one, _ = images.normalize(first, root / "images")
            second.write_bytes(payload)
            target_two, _ = images.normalize(second, root / "images")
            self.assertEqual(target_one, target_two)
            self.assertEqual(len(list((root / "images").iterdir())), 1)

    def test_conflicting_bytes_get_a_digest_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = root / "figure.png"
            one.write_bytes(support.png_bytes(4, 4, (1, 2, 3)))
            target_one, _ = images.normalize(one, root / "images")
            two_dir = root / "other"
            two_dir.mkdir()
            two = two_dir / "figure.png"
            two.write_bytes(support.png_bytes(4, 4, (200, 100, 50)))
            target_two, _ = images.normalize(two, root / "images")
            self.assertNotEqual(target_one, target_two)
            self.assertTrue(target_two.name.startswith("figure-"))


if __name__ == "__main__":
    unittest.main()
