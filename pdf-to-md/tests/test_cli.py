"""CLI behaviour and the end-to-end assembly path with the network stubbed out."""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from support import isolated_environment
import mineru_client
import organize
import pdf2md


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pdf2md.main(argv)
    return code, out.getvalue(), err.getvalue()


def fake_precision(pdf, workdir, token, **kwargs):
    """Pretend MinerU returned a zip with one PNG mis-named .jpg."""
    root = Path(workdir)
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "images" / "figure.jpg").write_bytes(support.png_bytes(24, 18))
    (root / "full.md").write_text("# Paper\n\n![Figure 1](images/figure.jpg)\n\nBody.\n",
                                  encoding="utf-8")
    (root / "content_list.json").write_text("[]", encoding="utf-8")
    return mineru_client.ParseResult("precision", root, root / "full.md",
                                     root / "content_list.json")


def fake_flash(pdf, workdir, **kwargs):
    root = Path(workdir)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{Path(pdf).stem}.md").write_text("# Paper\n\nBody only.\n", encoding="utf-8")
    return mineru_client.ParseResult("flash", root, root / f"{Path(pdf).stem}.md", None)


class FakeClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def complete(self, prompt):
        return prompt  # identity keeps references and structure intact


class UsageTests(unittest.TestCase):
    def test_no_input_prints_help_and_exits_2(self):
        with isolated_environment():
            code, out, err = run_cli([])
            self.assertEqual(code, 2)
            self.assertIn("usage", out.lower())
            self.assertIn("至少给一个 PDF", err)

    def test_missing_pdf_exits_2(self):
        with isolated_environment() as work:
            code, _, err = run_cli([str(work / "absent.pdf")])
            self.assertEqual(code, 2)
            self.assertIn("PDF 不存在", err)

    def test_duplicate_stems_exit_2(self):
        with isolated_environment() as work:
            one, two = work / "same.pdf", work / "same.PDF"
            for path in (one, two):
                path.write_bytes(b"%PDF-1.4\n")
            code, _, err = run_cli([str(one), str(two)])
            self.assertEqual(code, 2)
            self.assertIn("同一输出目录", err)

    def test_translate_without_credentials_exits_2(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            code, _, err = run_cli([str(pdf), "--translate", "--ocr", "flash"])
            self.assertEqual(code, 2)
            self.assertIn("翻译需要", err)

    def test_precision_without_token_exits_2(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            code, _, err = run_cli([str(pdf)])
            self.assertEqual(code, 2)
            self.assertIn("MinerU Token", err)

    def test_bad_header_argument_exits_2(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            code, _, err = run_cli([str(pdf), "--api-header", "broken"])
            self.assertEqual(code, 2)
            self.assertIn("--api-header", err)

    def test_doctor_offline_reports_missing_token(self):
        with isolated_environment():
            code, out, _ = run_cli(["--doctor", "--offline"])
            self.assertEqual(code, 1)
            self.assertIn("MinerU Token 缺失", out)


class AssemblyTests(unittest.TestCase):
    def test_precision_run_builds_the_contract_then_skips(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% sample\n")
            with patch.object(mineru_client, "precision_parse", side_effect=fake_precision) as patched:
                code, out, err = run_cli([str(pdf), "--mineru-token", "test-token"])
                self.assertEqual(code, 0, err)
                self.assertEqual(patched.call_count, 1)

                delivery = organize.delivery_paths(work, "paper")
                self.assertTrue(delivery.markdown.is_file())
                self.assertTrue(delivery.pdf_copy.is_file())
                self.assertTrue((delivery.images_dir / "figure.png").is_file())
                self.assertFalse((delivery.images_dir / "figure.jpg").exists())
                self.assertTrue(delivery.manifest.is_file())
                self.assertTrue(delivery.validation.is_file())
                self.assertTrue(delivery.completion.is_file())
                self.assertIn("images/figure.png", delivery.markdown.read_text(encoding="utf-8"))
                saved = json.loads(delivery.validation.read_text(encoding="utf-8"))
                self.assertTrue(saved["ok"], saved)

                # a second run must not re-parse, not even hit the OCR cache path twice
                code, out, err = run_cli([str(pdf), "--mineru-token", "test-token"])
                self.assertEqual(code, 0, err)
                self.assertEqual(patched.call_count, 1)
                self.assertIn("已有有效交付", out)

    def test_flash_run_needs_no_token_and_reports_no_figures(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            with patch.object(mineru_client, "flash_parse", side_effect=fake_flash):
                code, out, err = run_cli([str(pdf), "--ocr", "flash"])
            self.assertEqual(code, 0, err)
            delivery = organize.delivery_paths(work, "paper")
            meta = json.loads(delivery.manifest.read_text(encoding="utf-8"))
            self.assertEqual(meta["channel"], "flash")
            self.assertEqual(meta["images"], [])

    def test_translation_keeps_figures_and_writes_chinese(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            env = {"PDF2MD_BASE_URL": "https://api.example.com/v1",
                   "PDF2MD_MODEL": "demo-model", "PDF2MD_API_KEY": "sk-demo"}
            with patch.dict(os.environ, env), \
                    patch.object(mineru_client, "precision_parse", side_effect=fake_precision), \
                    patch.object(pdf2md.translation, "Client", FakeClient):
                code, out, err = run_cli([str(pdf), "--translate", "--mineru-token", "test-token"])
            self.assertEqual(code, 0, err)
            delivery = organize.delivery_paths(work, "paper")
            self.assertTrue(delivery.chinese.is_file())
            saved = json.loads(delivery.validation.read_text(encoding="utf-8"))
            self.assertTrue(saved["ok"], saved)
            self.assertEqual(saved["references"]["en"], saved["references"]["zh"])

    def test_json_summary_is_machine_readable(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            with patch.object(mineru_client, "flash_parse", side_effect=fake_flash):
                code, out, _ = run_cli([str(pdf), "--ocr", "flash", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out[out.index("{"):out.rindex("}") + 1])
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["results"][0]["channel"], "flash")


if __name__ == "__main__":
    unittest.main()
