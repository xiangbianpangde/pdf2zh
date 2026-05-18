"""Drive the running pdf2zh Gradio UI via HTTP to batch-process PDFs.

Equivalent to: open browser, drag all PDFs in, click "开始批量处理", wait.
Polls progress and prints concise status every 20s.

Usage:
    python tools/test_batch_via_ui.py <pdf_dir> [--url http://127.0.0.1:7860]
                                                [--workers 8]
"""
import argparse
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from gradio_client import Client, handle_file


def hh():
    return time.strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser(
        description="Drive the running pdf2zh Gradio UI to batch-process PDFs"
    )
    parser.add_argument("pdf_dir", type=Path, help="Directory of PDFs to submit")
    parser.add_argument("--url", default="http://127.0.0.1:7860", help="Gradio URL")
    parser.add_argument("--workers", type=int, default=8, help="Translation concurrency")
    parser.add_argument("--model", default="deepseek-chat",
                        help="Model name (deepseek-chat / qwen2.5:14b / ...)")
    parser.add_argument("--base-url", default="https://api.deepseek.com",
                        help="OpenAI-compatible endpoint base URL")
    parser.add_argument("--chunk-size", type=int, default=3000)
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    is_local = "localhost" in args.base_url or "127.0.0.1" in args.base_url
    if not api_key and not is_local:
        sys.exit("DEEPSEEK_API_KEY not set (required for remote backends)")
    if not api_key:
        api_key = "local"

    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs in {args.pdf_dir}")
    print(f"[{hh()}] {len(pdfs)} PDFs found in {args.pdf_dir}", flush=True)

    print(f"[{hh()}] connecting to {args.url} ...", flush=True)
    c = Client(args.url, verbose=False)

    print(f"[{hh()}] submitting batch ...", flush=True)
    job = c.submit(
        pdf_files=[handle_file(str(p)) for p in pdfs],
        md_files=None,
        do_convert=True,
        do_translate=True,
        reuse_server=True,
        backend="pipeline",
        lang="en",
        start_page=-1,
        end_page=-1,
        model=args.model,
        base_url=args.base_url,
        chunk_size=args.chunk_size,
        workers=args.workers,
        api_key=api_key,
        api_name="/run_workflow",
    )

    start_t = time.time()
    last_en = -1
    last_zh = -1
    last_tail_hash = None

    print(f"[{hh()}] submitted. polling ...\n", flush=True)
    while True:
        done = job.done()
        try:
            outs = job.outputs()
        except Exception as e:
            outs = []
            if done:
                print(f"[{hh()}] outputs() error after done: {e}", flush=True)

        if outs:
            log, en_files, zh_files = outs[-1]
            en_count = len(en_files) if en_files else 0
            zh_count = len(zh_files) if zh_files else 0
            tail = (log or "")[-300:]
            th = hash(tail)
            if (en_count != last_en or zh_count != last_zh or th != last_tail_hash):
                elapsed = (time.time() - start_t) / 60
                print(f"\n[{hh()}] elapsed={elapsed:.1f}min  "
                      f"EN={en_count}/{len(pdfs)}  ZH={zh_count}/{len(pdfs)}",
                      flush=True)
                for line in (log or "").split("\n")[-6:]:
                    if line.strip():
                        print(f"  | {line}", flush=True)
                last_en, last_zh, last_tail_hash = en_count, zh_count, th

        if done:
            break
        time.sleep(20)

    print(f"\n[{hh()}] === DONE ===", flush=True)
    try:
        final = job.result()
        if final:
            log, en_files, zh_files = final
            print(f"  English MDs: {len(en_files) if en_files else 0}/{len(pdfs)}",
                  flush=True)
            print(f"  Chinese MDs: {len(zh_files) if zh_files else 0}/{len(pdfs)}",
                  flush=True)
            print(f"  Total time: {(time.time()-start_t)/60:.1f} min", flush=True)
    except Exception as e:
        print(f"  result() error: {e}", flush=True)


if __name__ == "__main__":
    main()
