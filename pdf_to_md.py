"""PDF -> English Markdown via MinerU.

Usage:
    python pdf_to_md.py <pdf_path> [-o output_dir] [-b backend] [-l lang]
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Convert PDF to Markdown using MinerU")
    parser.add_argument("pdf_path", type=Path, help="Input PDF file")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("./output"),
                        help="Output directory (default: ./output)")
    parser.add_argument("-b", "--backend",
                        default="pipeline",
                        choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine",
                                 "vlm-http-client", "hybrid-http-client"],
                        help="MinerU backend (default: pipeline, lowest VRAM)")
    parser.add_argument("-l", "--lang", default="en",
                        help="OCR language hint for pipeline/hybrid backends (default: en)")
    parser.add_argument("-s", "--start", type=int, default=None, help="Start page (0-indexed)")
    parser.add_argument("-e", "--end", type=int, default=None, help="End page (0-indexed, inclusive)")
    args = parser.parse_args()

    pdf = args.pdf_path.resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir.resolve()

    cmd = ["mineru", "-p", str(pdf), "-o", str(out), "-b", args.backend, "-l", args.lang]
    if args.start is not None:
        cmd += ["-s", str(args.start)]
    if args.end is not None:
        cmd += ["-e", str(args.end)]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"mineru failed with exit code {result.returncode}")

    md_files = list((out / pdf.stem).rglob(f"{pdf.stem}.md"))
    if md_files:
        print(f"\nMarkdown ready: {md_files[0]}")
    else:
        print(f"\nDone. Check output in: {out / pdf.stem}")


if __name__ == "__main__":
    main()
