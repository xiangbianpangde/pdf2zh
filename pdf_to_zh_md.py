"""PDF -> Chinese Markdown (MinerU + DeepSeek one-shot pipeline).

Usage:
    set DEEPSEEK_API_KEY=sk-xxxxxxxx
    python pdf_to_zh_md.py <pdf_path> [-o output_dir] [-b backend]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

from translate_md import chunk_markdown, translate_chunks_concurrent


def main():
    parser = argparse.ArgumentParser(description="PDF -> Chinese Markdown via MinerU + DeepSeek")
    parser.add_argument("pdf_path", type=Path, help="Input PDF file")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("./output"),
                        help="Output directory (default: ./output)")
    parser.add_argument("-b", "--backend",
                        default="pipeline",
                        choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                        help="MinerU backend (default: pipeline)")
    parser.add_argument("-l", "--lang", default="en", help="OCR language hint")
    parser.add_argument("--model", default="deepseek-chat", help="DeepSeek model")
    parser.add_argument("--chunk-size", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=8,
                        help="Concurrent translation requests (default 8)")
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY"))
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--skip-translate", action="store_true",
                        help="Only run PDF -> English MD, skip translation")
    args = parser.parse_args()

    if not args.skip_translate and not args.api_key:
        sys.exit("Missing API key. Set DEEPSEEK_API_KEY or pass --api-key")

    pdf = args.pdf_path.resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir.resolve()

    print(f"[1/2] PDF -> Markdown (backend={args.backend})")
    cmd = ["mineru", "-p", str(pdf), "-o", str(out), "-b", args.backend, "-l", args.lang]
    print(f"      $ {' '.join(cmd)}")
    if subprocess.run(cmd).returncode != 0:
        sys.exit("mineru failed")

    md_files = list((out / pdf.stem).rglob(f"{pdf.stem}.md"))
    if not md_files:
        sys.exit(f"No markdown found under {out / pdf.stem}")
    en_md = md_files[0]
    print(f"      English markdown: {en_md}")

    if args.skip_translate:
        return

    print(f"\n[2/2] Translating with {args.model} (workers={args.workers})")
    md = en_md.read_text(encoding="utf-8")
    chunks = chunk_markdown(md, args.chunk_size)
    print(f"      {len(chunks)} chunks")

    client = OpenAI(api_key=args.api_key, base_url=args.base_url)

    def on_progress(done, total, idx):
        print(f"      [{done}/{total}] chunk #{idx + 1} ok", flush=True)

    translated = translate_chunks_concurrent(
        client, chunks, model=args.model,
        max_workers=args.workers, on_progress=on_progress,
    )

    zh_md = en_md.with_name(en_md.stem + "_zh.md")
    zh_md.write_text("\n\n".join(translated), encoding="utf-8")
    print(f"\nDone: {zh_md}")


if __name__ == "__main__":
    main()
