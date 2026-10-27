"""Failure paths: a failed stage must not leave a half-built delivery behind."""
from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401 - sets sys.path
from support import isolated_environment
import mineru_client
import organize
from errors import TranslationError
import pdf2md


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pdf2md.main(argv)
    return code, out.getvalue(), err.getvalue()


class FailurePathTests(unittest.TestCase):
    def test_ocr_failure_leaves_no_delivery_and_prunes_empty_state(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            out_root = work / "out"
            with patch.object(mineru_client, "precision_parse",
                              side_effect=mineru_client.MineruError("Token 无效", code="A0202")):
                code, _, err = run_cli([str(pdf), "-o", str(out_root),
                                        "--mineru-token", "bad"])
            self.assertEqual(code, 1)
            self.assertIn("Token 无效", err)
            self.assertFalse((out_root / "paper").exists())
            self.assertFalse((out_root / ".cache").exists())

    def test_translation_failure_keeps_english_and_writes_no_chinese(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            out_root = work / "out"

            def fake_precision(pdf_path, workdir, token, **kwargs):
                root = Path(workdir)
                (root / "images").mkdir(parents=True, exist_ok=True)
                (root / "images" / "f.png").write_bytes(support.png_bytes())
                (root / "full.md").write_text("![a](images/f.png)\n", encoding="utf-8")
                return mineru_client.ParseResult("precision", root, root / "full.md", None)

            class Broken:
                def __init__(self, endpoint):
                    self.endpoint = endpoint

                def complete(self, prompt):
                    raise TranslationError("endpoint down")

            with patch.object(mineru_client, "precision_parse", side_effect=fake_precision), \
                    patch.object(pdf2md.translation, "Client", Broken), \
                    patch.dict("os.environ", {"PDF2MD_BASE_URL": "https://api.example.com/v1",
                                              "PDF2MD_MODEL": "m", "PDF2MD_API_KEY": "k"}):
                code, _, err = run_cli([str(pdf), "-o", str(out_root),
                                        "--mineru-token", "t", "--translate"])
            self.assertEqual(code, 1)
            delivery = organize.delivery_paths(out_root, "paper")
            self.assertTrue(delivery.markdown.is_file())
            self.assertFalse(delivery.chinese.exists())
            self.assertFalse(delivery.completion.exists())

    def test_colliding_output_of_another_pdf_is_refused(self):
        with isolated_environment() as work:
            pdf = work / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% first\n")
            out_root = work / "out"
            delivery = organize.delivery_paths(out_root, "paper")
            delivery.out_dir.mkdir(parents=True)
            delivery.pdf_copy.write_bytes(b"%PDF-1.4\n% different\n")
            with patch.object(mineru_client, "precision_parse") as patched:
                code, _, err = run_cli([str(pdf), "-o", str(out_root),
                                        "--mineru-token", "t"])
            self.assertEqual(code, 1)
            self.assertIn("另一份 PDF", err)
            self.assertEqual(patched.call_count, 0)


if __name__ == "__main__":
    unittest.main()
