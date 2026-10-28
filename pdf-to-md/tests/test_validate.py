"""Delivery validation: every hard rule fails the delivery instead of warning."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
import validate


def build(root: Path, *, english: str, chinese: str | None = None,
          figures: dict[str, bytes] | None = None) -> validate.Report:
    (root / "images").mkdir(parents=True, exist_ok=True)
    for name, payload in (figures or {"figure.png": support.png_bytes()}).items():
        (root / "images" / name).write_bytes(payload)
    en_md = root / "paper.md"
    en_md.write_text(english, encoding="utf-8")
    zh_md = None
    if chinese is not None:
        zh_md = root / "paper_zh.md"
        zh_md.write_text(chinese, encoding="utf-8")
    return validate.validate(en_md, zh_md, root / "images")


class HardRuleTests(unittest.TestCase):
    def test_valid_delivery_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="![a](images/figure.png)\n",
                           chinese="![a](images/figure.png)\n")
            self.assertTrue(report.ok, report.as_dict())
            self.assertEqual(report.image_count, 1)

    def test_missing_figure_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="![a](images/nope.png)\n")
            self.assertFalse(report.ok)
            self.assertIn("missing_image", report.codes())

    def test_external_reference_is_not_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="![a](https://example.com/a.png)\n")
            self.assertIn("external_image", report.codes())

    def test_absolute_and_parent_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp),
                           english="![a](/abs/a.png)\n\n![b](../outside/b.png)\n")
            codes = report.codes()
            self.assertIn("non_portable_image_path", codes)

    def test_chinese_must_keep_the_same_figure_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(
                Path(tmp),
                english="![one](images/one.png)\n\n![two](images/two.png)\n",
                chinese="![two](images/two.png)\n\n![one](images/one.png)\n",
                figures={"one.png": support.png_bytes(), "two.png": support.png_bytes(9, 9)},
            )
            self.assertFalse(report.ok)
            self.assertIn("image_reference_parity_mismatch", report.codes())

    def test_suffix_must_match_real_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="![a](images/figure.png)\n",
                           figures={"figure.png": support.png_bytes()})
            # rename to a mismatching suffix but keep PNG bytes
            root = Path(tmp)
            (root / "images" / "figure.png").rename(root / "images" / "figure.jpg")
            report = validate.validate(root / "paper.md", None, root / "images")
            self.assertIn("extension_mismatch", report.codes())

    def test_undecodable_asset_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="![a](images/figure.png)\n",
                           figures={"figure.png": b"\x89PNG\r\n\x1a\ntruncated"})
            self.assertIn("undecodable_image", report.codes())

    def test_empty_markdown_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build(Path(tmp), english="")
            self.assertIn("missing_markdown", report.codes())


if __name__ == "__main__":
    unittest.main()
