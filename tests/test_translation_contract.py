"""Offline regression checks for the frozen translation runtime."""
import contextlib
import io
import json
import threading
import time
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
    def test_translation_chunk_checkpoint_resumes_only_missing_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / 'chunks.json'
            chunks = ['one', 'two', 'three']
            client = object()
            calls = []
            fail_once = [True]
            from translate_md import translate_chunks_concurrent
            def fake_translate(_client, chunk, model='x', retries=3):
                calls.append(chunk)
                if chunk == 'two' and fail_once[0]:
                    fail_once[0] = False
                    raise RuntimeError('injected interruption')
                return f'zh:{chunk}'
            with patch.object(sys.modules['translate_md'], 'translate_chunk', side_effect=fake_translate):
                with self.assertRaises(RuntimeError):
                    translate_chunks_concurrent(client, chunks, model='m', max_workers=1,
                                                checkpoint_path=checkpoint, checkpoint_context='ctx')
                self.assertEqual(json.loads(checkpoint.read_text())['results'], {'0': 'zh:one'})
                resumed = translate_chunks_concurrent(client, chunks, model='m', max_workers=1,
                                                      checkpoint_path=checkpoint, checkpoint_context='ctx')
            self.assertEqual(resumed, ['zh:one', 'zh:two', 'zh:three'])
            self.assertEqual(calls.count('one'), 1)
            calls.clear()
            with patch.object(sys.modules['translate_md'], 'translate_chunk', side_effect=fake_translate):
                changed = translate_chunks_concurrent(client, chunks, model='m', max_workers=1,
                                                       checkpoint_path=checkpoint, checkpoint_context='changed')
            self.assertEqual(changed, resumed)
            self.assertEqual(calls, chunks)

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

    def test_batch_cli_processes_papers_concurrently(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = [Path(tmp) / f'paper{i}.pdf' for i in range(3)]
            for pdf in pdfs:
                pdf.write_bytes(b'%PDF-1.4')
            active = 0
            maximum = 0
            lock = threading.Lock()
            def fake_process(pdf, out_root, **kwargs):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.04)
                with lock:
                    active -= 1
                return SimpleNamespace(skipped=False, translated=True,
                                       out_dir=Path(out_root) / pdf.stem, n_images=0)
            with patch.object(pdf_to_zh_md.pipeline, 'process', side_effect=fake_process), contextlib.redirect_stdout(io.StringIO()):
                code = pdf_to_zh_md.main([*(str(pdf) for pdf in pdfs), '-o', tmp,
                                          '--parallelism', '2'])
            self.assertEqual(code, 0)
            self.assertEqual(maximum, 2)

    def test_batch_cli_collects_unexpected_worker_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'error.pdf'
            pdf.write_bytes(b'%PDF-1.4')
            with patch.object(pdf_to_zh_md.pipeline, 'process', side_effect=RuntimeError('injected')):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(pdf_to_zh_md.main([str(pdf), '-o', tmp]), 1)

    def test_batch_cli_rejects_output_stem_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdfs = [Path(tmp) / name for name in ('same.pdf', 'same.PDF')]
            for pdf in pdfs:
                pdf.write_bytes(b'%PDF-1.4')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(pdf_to_zh_md.main([*(str(pdf) for pdf in pdfs), '-o', tmp]), 2)


if __name__ == '__main__':
    unittest.main()
