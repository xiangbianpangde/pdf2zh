#!/usr/bin/env python3
"""Real interruption test: kill the CLI mid-translation, then resume.

The whole point is that this is *not* a unit test: it starts the real
``scripts/pdf2md.py`` as a child process, serves it from a local stub endpoint
that records every request, kills the process group with SIGKILL after a few
chunks, and then restarts the identical command.

Pass criteria:

* the first run leaves a checkpoint with K completed chunks (0 < K < total);
* the second run re-requests exactly (total - K) chunks;
* **no chunk that already had a result is requested again** — the only allowed
  repeats are the requests that were in flight when SIGKILL landed, because a
  killed request never produced a result to checkpoint;
* the final Chinese Markdown exists, validates, and the checkpoint is cleaned up.

Offline and deterministic: the OCR stage is served from a seeded cache, so no
MinerU call happens. Runtime is a few seconds.

    python3 tests/resume_smoke.py [--kill-after N] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import organize  # noqa: E402
import pdf2md  # noqa: E402
import support  # noqa: E402
import validate  # noqa: E402

PARAGRAPHS = 10
CHUNK_SIZE = 1000
STUB_DELAY = 1.2


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                prompt = message.get("content", "")
        self.server.requests.append(prompt)      # type: ignore[attr-defined]
        time.sleep(self.server.delay)            # type: ignore[attr-defined]
        payload = {"choices": [{"message": {"content": prompt}}]}
        raw = json.dumps(payload).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.server.completed.append(prompt)  # type: ignore[attr-defined]
        except (BrokenPipeError, ConnectionResetError):
            pass  # the parent killed the client mid-flight; that is the scenario

    def log_message(self, *args):
        return


class Stub:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        self.server.requests = []
        self.server.completed = []
        self.server.delay = STUB_DELAY
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()

    @property
    def requests(self) -> list[str]:
        return self.server.requests

    @property
    def completed(self) -> list[str]:
        return self.server.completed

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


def seed_ocr_cache(pdf: Path, out_root: Path, markdown: str) -> None:
    """Pretend a flash parse already happened, so the run never touches MinerU."""
    stem = organize.safe_stem(pdf)
    delivery = organize.delivery_paths(out_root, stem)
    source_sha = organize.sha256_file(pdf)
    params = {"channel": "flash", "ocr_model": "vlm", "language": "en", "pages": None}
    directory = pdf2md._cache_dir(delivery, params, source_sha)
    extracted = directory / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / "full.md").write_text(markdown, encoding="utf-8")
    (extracted / "images").mkdir(exist_ok=True)
    (extracted / "images" / "fig.png").write_bytes(support.png_bytes(20, 20))
    organize.atomic_json(directory / "receipt.json", {
        "source": source_sha, "params": params,
        "relative": "extracted", "created": int(time.time()),
    })
    (directory / "READY").write_text("ok\n", encoding="utf-8")


def command(pdf: Path, out_root: Path, stub_url: str) -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / "pdf2md.py"), str(pdf),
            "-o", str(out_root), "--ocr", "flash", "--translate",
            "--base-url", stub_url, "--model", "stub-model", "--api-key", "stub-key",
            "--chunk-size", str(CHUNK_SIZE), "--workers", "2"]


def main() -> int:
    parser = argparse.ArgumentParser(description="真实中断续跑验证")
    parser.add_argument("--kill-after", type=int, default=2, help="收到几个请求后杀进程")
    parser.add_argument("--verbose", action="store_true", help="打印子进程输出")
    args = parser.parse_args()

    body = "\n\n".join(f"Paragraph {index}. " + "word " * 70 for index in range(1, PARAGRAPHS + 1))
    markdown = f"# Interruption test\n\n{body}\n\n![fig](images/fig.png)\n"
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp, Stub() as stub:
        work = Path(tmp)
        pdf = work / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% resume smoke\n")
        out_root = work / "out"
        seed_ocr_cache(pdf, out_root, markdown)

        delivery = organize.delivery_paths(out_root, organize.safe_stem(pdf))
        cmd = command(pdf, out_root, stub.base)

        # ---- run 1: start, wait for a few chunks, SIGKILL the whole group ----
        child = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True)
        deadline = time.time() + 180
        while time.time() < deadline and len(stub.completed) < args.kill_after:
            if child.poll() is not None:
                break
            time.sleep(0.05)
        served_before_kill = len(stub.requests)
        completed_before_kill = set(stub.completed)
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        first_output = child.communicate(timeout=30)[0] or ""

        checkpoint = next(iter(delivery.checkpoint_dir.glob("*.json")), None) if delivery.checkpoint_dir.is_dir() else None
        if checkpoint is None:
            failures.append("失败：第一次运行没有留下检查点")
            saved: dict = {}
        else:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        completed = {int(key): value for key, value in (saved.get("results") or {}).items()}
        total_chunks = len(saved.get("chunks") or [])
        print(f"run1: 已接收 {served_before_kill} 次请求，已完成 {len(completed_before_kill)} 次，"
              f"检查点 {len(completed)} 块（总块数 {total_chunks or '未知'}）")
        if not completed:
            failures.append("失败：检查点为空，杀得太早或检查点没落盘")
        if total_chunks and len(completed) >= total_chunks:
            failures.append("失败：杀得太晚，所有块都已完成，无法验证续跑")

        # ---- run 2: identical command, must finish the remainder ----
        second = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        resumed_requests = stub.requests[served_before_kill:]
        print(f"run2: exit={second.returncode}，续跑请求 {len(resumed_requests)} 次")

        resumed_logged = "续跑：已恢复" in second.stdout
        print(f"run2: 日志出现「续跑：已恢复」= {resumed_logged}")
        if not resumed_logged:
            failures.append("失败：第二次运行没有从检查点续跑")

        expected_remaining = total_chunks - len(completed) if total_chunks else None
        if expected_remaining is not None and len(resumed_requests) != expected_remaining:
            failures.append(f"失败：续跑请求数 {len(resumed_requests)} ≠ 缺失块数 {expected_remaining}")

        repeats = [prompt for prompt in resumed_requests if prompt in completed_before_kill]
        total_duplicates = len(stub.requests) - len(set(stub.requests))
        print(f"全程请求 {len(stub.requests)} 次；已完成的块被重复请求 {len(repeats)} 次；"
              f"在途丢失后重发 {total_duplicates} 次")
        if repeats:
            failures.append(f"失败：{len(repeats)} 个已有结果的块被重复请求")
        if total_duplicates > 2:
            failures.append(f"失败：重发次数 {total_duplicates} 超过在途上限 2")

        report = validate.validate(delivery.markdown, delivery.chinese, delivery.images_dir)
        print(f"最终交付：zh 存在={delivery.chinese.is_file()}，validation.ok={report.ok}，"
              f"issues={report.codes()}")
        if not delivery.chinese.is_file():
            failures.append("失败：没有生成中文 Markdown")
        if not report.ok:
            failures.append(f"失败：校验未通过 {report.codes()}")
        if checkpoint is not None and checkpoint.exists():
            failures.append("失败：完成后检查点没有清理")

        if args.verbose:
            print("---- run1 输出 ----")
            print(first_output.strip()[-2000:])
            print("---- run2 输出 ----")
            print(second.stdout.strip()[-2000:])

    print("-" * 68)
    if failures:
        for line in failures:
            print(f"  ✗ {line}")
        return 1
    print("  ✓ 真实中断续跑通过：只补缺失块，无重复请求，最终交付有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
