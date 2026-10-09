"""Offline regression checks for the frozen translation runtime."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from translate_md import chunk_markdown
import pdf_to_zh_md


class TranslationContractTests(unittest.TestCase):
    def test_closed_delimiters_do_not_disable_chunking(self):
        for prefix in ['$$x=1$$', '```python\nx=1\n```', '~~~python\nx=1\n~~~']:
            with self.subTest(prefix=prefix):
                md = prefix + '\n\n' + '\n\n'.join(['ordinary text ' * 15] * 12)
                chunks = chunk_markdown(md, 300)
                self.assertGreater(len(chunks), 1)
                self.assertLessEqual(max(map(len, chunks)), 300)
                self.assertEqual('\n\n'.join(chunks), md)

    def test_no_cut_inside_multiline_fence_or_math(self):
        for atom in ['````python\nx=1\n\n```\n\ny=2\n````', '$$\nx=1\n\ny=2\n$$']:
            md = 'intro ' * 40 + '\n\n' + atom + '\n\n' + 'tail ' * 40
            chunks = chunk_markdown(md, 200)
            self.assertTrue(any(atom in chunk for chunk in chunks))
            self.assertEqual('\n\n'.join(chunks), md)

    def test_successful_ocr_only_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'paper.pdf'
            pdf.write_bytes(b'%PDF-1.4')
            result = SimpleNamespace(skipped=False, translated=False, out_dir=Path(tmp), n_images=0)
            with patch.object(pdf_to_zh_md.pipeline, 'process', return_value=result), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pdf_to_zh_md.main([str(pdf), '-o', tmp, '--skip-translate']), 0)


if __name__ == '__main__':
    unittest.main()
