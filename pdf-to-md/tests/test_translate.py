"""Chunking rules, reasoning cleanup, and checkpoint resume semantics."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import translate


ENDPOINT = translate.Endpoint(base_url="http://127.0.0.1:9/v1", model="fake-model",
                              api_key="fake-key")


class FakeClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.endpoint = ENDPOINT
        self.calls: list[str] = []
        self.fail_on = fail_on

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.fail_on and self.fail_on in prompt:
            self.fail_on = None
            raise RuntimeError("injected interruption")
        return f"zh:{prompt}"


class ChunkingTests(unittest.TestCase):
    def test_closed_delimiters_do_not_disable_chunking(self):
        for prefix in ("$$x=1$$", "```python\nx=1\n```", "~~~python\nx=1\n~~~"):
            with self.subTest(prefix=prefix):
                md = prefix + "\n\n" + "\n\n".join(["ordinary text " * 15] * 12)
                chunks = translate.split_markdown(md, 300)
                self.assertGreater(len(chunks), 1)
                self.assertLessEqual(max(map(len, chunks)), 300)
                self.assertEqual("\n\n".join(chunks), md)

    def test_no_cut_inside_multiline_fence_or_math(self):
        for atom in ("````python\nx=1\n\n```\n\ny=2\n````", "$$\nx=1\n\ny=2\n$$"):
            with self.subTest(atom=atom[:6]):
                md = "intro " * 40 + "\n\n" + atom + "\n\n" + "tail " * 40
                chunks = translate.split_markdown(md, 200)
                self.assertTrue(any(atom in chunk for chunk in chunks))
                self.assertEqual("\n\n".join(chunks), md)

    def test_single_oversized_block_is_not_split(self):
        md = "x" * 500
        self.assertEqual(translate.split_markdown(md, 100), [md])


class ReasoningTests(unittest.TestCase):
    def test_closed_and_truncated_think_blocks_are_removed(self):
        self.assertEqual(translate.clean_reasoning("<think>a\nb</think>正文"), "正文")
        self.assertEqual(translate.clean_reasoning("<think>a\nb"), "")
        self.assertEqual(translate.clean_reasoning("没有推理"), "没有推理")


class CheckpointTests(unittest.TestCase):
    def test_resume_only_translates_missing_chunks(self):
        chunks = ["one", "two", "three"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = translate.Checkpoint(path)
            client = FakeClient(fail_on="two")
            with self.assertRaises(RuntimeError):
                translate.translate_text("\n\n".join(chunks), client, chunk_size=5,
                                         workers=1, checkpoint=checkpoint, context="ctx")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(saved["results"]), ["0"])

            resumed = FakeClient()
            text = translate.translate_text("\n\n".join(chunks), resumed, chunk_size=5,
                                            workers=1, checkpoint=checkpoint, context="ctx")
            self.assertEqual(text, "zh:one\n\nzh:two\n\nzh:three")
            self.assertEqual(resumed.calls, ["two", "three"])

    def test_changed_context_invalidates_the_checkpoint(self):
        chunks = ["one"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            checkpoint = translate.Checkpoint(path)
            translate.translate_text("one", FakeClient(), workers=1,
                                     checkpoint=checkpoint, context="first")
            client = FakeClient()
            translate.translate_text("one", client, workers=1,
                                     checkpoint=checkpoint, context="second")
            self.assertEqual(client.calls, ["one"])

    def test_parallel_run_keeps_document_order(self):
        chunks = [f"block-{index}" for index in range(8)]
        client = FakeClient()
        text = translate.translate_text("\n\n".join(chunks), client, chunk_size=8,
                                        workers=4, checkpoint=None, context="ctx")
        self.assertEqual(text, "\n\n".join(f"zh:{chunk}" for chunk in chunks))


class HeaderTests(unittest.TestCase):
    def test_api_header_parsing(self):
        self.assertEqual(translate.parse_header("X-Foo: bar"), ("X-Foo", "bar"))
        with self.assertRaises(ValueError):
            translate.parse_header("broken")

    def test_opencode_session_header_only_for_opencode(self):
        endpoint = translate.Endpoint(base_url="https://opencode.ai/zen/go/v1",
                                      model="m", api_key="k")
        self.assertIn("x-opencode-session", endpoint.headers)
        other = translate.Endpoint(base_url="https://api.example.com/v1", model="m", api_key="k")
        self.assertNotIn("x-opencode-session", other.headers)


if __name__ == "__main__":
    unittest.main()
