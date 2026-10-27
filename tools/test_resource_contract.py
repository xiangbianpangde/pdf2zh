"""Regression tests for PDF delivery resources and output contracts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline
from html_output import render_markdown_file
from resource_contract import create_delivery_zip, extract_image_refs, validate_delivery


class ResourceContractTests(unittest.TestCase):
    def _make_pdf(self, root: Path, name: str = "paper.pdf") -> Path:
        pdf = root / name
        pdf.write_bytes(b"%PDF-1.4\n% test\n")
        return pdf

    def _make_png_with_wrong_extension(self, path: Path) -> None:
        image = Image.new("RGB", (12, 8), (220, 120, 40))
        image.save(path, format="PNG")

    def test_minimal_layout_normalizes_image_extension_and_rewrites_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            assets = extracted / "assets"
            assets.mkdir(parents=True)
            self._make_png_with_wrong_extension(assets / "figure.jpg")
            (assets / "diagram.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8"></svg>',
                encoding="utf-8",
            )
            (extracted / "full.md").write_text(
                "# Paper\n\n"
                "![Figure](assets/figure.jpg)\n\n"
                '<img src="assets/diagram.svg" alt="diagram">\n',
                encoding="utf-8",
            )
            pdf = self._make_pdf(root)
            out_dir = root / "output" / "paper"

            result = pipeline.organize(pdf, extracted, out_dir, layout="minimal")

            self.assertTrue((out_dir / "images" / "figure.png").is_file())
            self.assertTrue((out_dir / "images" / "diagram.svg").is_file())
            self.assertFalse((out_dir / "_mineru").exists())
            text = result.en_md.read_text(encoding="utf-8")
            self.assertIn("images/figure.png", text)
            self.assertIn("images/diagram.svg", text)
            report = validate_delivery(result.en_md, None, result.images_dir)
            self.assertTrue(report["ok"], report)

    def test_audit_layout_writes_manifest_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            extracted.mkdir()
            image = Image.new("RGB", (5, 6), "white")
            image.save(extracted / "figure.png", format="PNG")
            (extracted / "full.md").write_text("![x](figure.png)\n", encoding="utf-8")
            (extracted / "layout.json").write_text("{}", encoding="utf-8")
            pdf = self._make_pdf(root)
            out_dir = root / "output" / "paper"

            pipeline.organize(pdf, extracted, out_dir, layout="audit")

            manifest = out_dir / "manifest.json"
            validation = out_dir / "validation.json"
            self.assertTrue(manifest.is_file())
            self.assertTrue(validation.is_file())
            self.assertIn("figure.png", json.dumps(json.loads(manifest.read_text())))
            self.assertTrue(json.loads(validation.read_text())["ok"])
            self.assertTrue((out_dir / "_mineru" / "layout.json").is_file())

    def test_html_renderer_keeps_tables_and_relative_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / "paper_zh.md"
            html = root / "paper_zh.html"
            md.write_text(
                "# 标题\n\n"
                "![图](images/figure.png)\n\n"
                "| A | B |\n| --- | --- |\n| 1 | 2 |\n",
                encoding="utf-8",
            )

            output = render_markdown_file(md, html)

            self.assertEqual(output, html)
            text = html.read_text(encoding="utf-8")
            self.assertIn("<table>", text)
            self.assertIn('src="images/figure.png"', text)
            self.assertIn("background: #fff", text)

    def test_image_reference_order_mismatch_is_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            for name in ("one.png", "two.png"):
                Image.new("RGB", (8, 8), "white").save(images / name)
            en = root / "paper_en.md"
            zh = root / "paper_zh.md"
            en.write_text("![one](images/one.png)\\n![two](images/two.png)\\n", encoding="utf-8")
            zh.write_text("![two](images/two.png)\\n![one](images/one.png)\\n", encoding="utf-8")
            report = validate_delivery(en, zh, images)
            self.assertFalse(report["ok"])
            self.assertTrue(any(item["code"] == "image_reference_parity_mismatch" for item in report["issues"]))

    def test_external_image_is_not_portable_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            en = root / "paper_en.md"
            en.write_text("![remote](https://example.com/figure.png)\\n", encoding="utf-8")
            report = validate_delivery(en, None, images)
            self.assertFalse(report["ok"])
            self.assertTrue(any(item["code"] == "external_image" for item in report["issues"]))

    def test_delivery_zip_contains_only_self_contained_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "paper"
            (out / "images").mkdir(parents=True)
            (out / "images" / "figure.png").write_bytes(b"png")
            (out / "paper_en.md").write_text("x", encoding="utf-8")
            (out / "paper.pdf").write_bytes(b"pdf")
            (root / ".cache").mkdir()
            (root / ".cache" / "secret.txt").write_text("not delivery", encoding="utf-8")
            archive = create_delivery_zip(out, root / "paper.zip")
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            self.assertIn("paper/images/figure.png", names)
            self.assertNotIn(".cache/secret.txt", names)

    def test_incomplete_delivery_is_not_reported_as_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            en = root / "paper_en.md"
            zh = root / "paper_zh.md"
            en.write_text("![x](images/missing.png)\n", encoding="utf-8")
            zh.write_text("![x](images/missing.png)\n", encoding="utf-8")

            report = validate_delivery(en, zh, images)

            self.assertFalse(report["ok"])
            self.assertTrue(any(item["code"] == "missing_image" for item in report["issues"]))


if __name__ == "__main__":
    unittest.main()
