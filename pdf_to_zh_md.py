"""PDF -> Chinese Markdown（MinerU 云 API / 本地 + MiniMax-M3 一站式管线）。

Usage:
    python pdf_to_zh_md.py <pdf_path> [-o output_dir] [--ocr api|local]

OCR 两种模式：
    api    默认。MinerU 云 API（v4 Precision，Token 鉴权），不占本机算力、无需下模型
    local  回退。调本地 mineru CLI（需已安装模型）

翻译凭据解析顺序（未显式传 --api-key 时）：
    1. <本项目>/.secrets/minimax.json 的 "key"
    2. /Users/xbpd/WorkBuddy/每日早报/.secrets/minimax.json
    3. 环境变量 MINIMAX_API_KEY / OPENROUTER_API_KEY / DEEPSEEK_API_KEY
    （环境变量残留过期 key 时，.secrets 优先——实测踩坑）
"""
import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

from translate_md import chunk_markdown, translate_chunks_concurrent

import mineru_api

log = logging.getLogger("pdf2zh")

PROJECT_DIR = Path(__file__).resolve().parent
MINIMAX_SECRETS = [
    PROJECT_DIR / ".secrets" / "minimax.json",
    Path("/Users/xbpd/WorkBuddy/每日早报/.secrets/minimax.json"),
]


def load_api_key() -> str | None:
    """隐式解析 API key：.secrets 优先于环境变量（环境变量可能残留过期值）。"""
    for p in MINIMAX_SECRETS:
        if not p.exists():
            continue
        try:
            key = str(json.loads(p.read_text(encoding="utf-8")).get("key", "")).strip()
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("读取凭据 %s 失败: %s", p, exc)
            continue
        if key:
            return key
    for var in ("MINIMAX_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        val = os.getenv(var, "").strip()
        if val:
            return val
    return None


def ocr_via_api(pdf: Path, out_dir: Path) -> Path:
    """MinerU 云 API OCR：产物解包到 out/<stem>/，full.md 规范化为 <stem>.md。"""
    workdir = out_dir / pdf.stem
    md = mineru_api.precision_extract(pdf, workdir)
    target = workdir / f"{pdf.stem}.md"
    if md != target:
        if target.exists():
            target.unlink()
        md.rename(target)
    return target


def ocr_via_local(pdf: Path, out_dir: Path, backend: str, lang: str) -> Path:
    """本地 mineru CLI OCR（回退路径，需已安装本地模型）。"""
    mineru_bin = os.path.join(os.path.dirname(sys.executable), "mineru")
    if not os.path.exists(mineru_bin):
        mineru_bin = "mineru"
    cmd = [mineru_bin, "-p", str(pdf), "-o", str(out_dir), "-b", backend, "-l", lang]
    log.info("$ %s", " ".join(cmd))
    if subprocess.run(cmd).returncode != 0:
        sys.exit("mineru failed")
    # 用 walk 精确匹配（rglob 的 pattern 会把 [ ] 当 glob 字符，导致方括号文件名失败）
    md_files = []
    for root, _, files in os.walk(out_dir / pdf.stem):
        for f in files:
            if f == f"{pdf.stem}.md":
                md_files.append(Path(root) / f)
    if not md_files:
        sys.exit(f"No markdown found under {out_dir / pdf.stem}")
    return md_files[0]


def main():
    parser = argparse.ArgumentParser(
        description="PDF -> 中文 Markdown via MinerU 云API/本地 + MiniMax-M3")
    parser.add_argument("pdf_path", type=Path, help="Input PDF file")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("./output"),
                        help="Output directory (default: ./output)")
    parser.add_argument("--ocr", default="api", choices=["api", "local"],
                        help="OCR 模式（默认 api：MinerU 云 API；local：本地 mineru CLI）")
    parser.add_argument("-b", "--backend",
                        default="pipeline",
                        choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                        help="本地 mineru backend（仅 --ocr local 时生效，默认 pipeline）")
    parser.add_argument("-l", "--lang", default="en", help="OCR language hint")
    parser.add_argument("--model", default=os.getenv("MINIMAX_MODEL", "MiniMax-M3"),
                        help="翻译模型（默认 MiniMax-M3）")
    parser.add_argument("--chunk-size", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=8,
                        help="Concurrent translation requests (default 8)")
    parser.add_argument("--api-key", default=None,
                        help="显式翻译 API key（不传则按 .secrets > 环境变量顺序解析）")
    parser.add_argument("--base-url", default=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"))
    parser.add_argument("--skip-translate", action="store_true",
                        help="Only run PDF -> English MD, skip translation")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    api_key = args.api_key or load_api_key()
    if not args.skip_translate and not api_key:
        sys.exit("Missing API key. 传 --api-key，或在 .secrets/minimax.json / 环境变量中配置")

    pdf = args.pdf_path.resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    log.info("[1/2] PDF -> Markdown (ocr=%s)", args.ocr)
    if args.ocr == "api":
        try:
            en_md = ocr_via_api(pdf, out)
        except mineru_api.MinerUError as exc:
            sys.exit(f"MinerU 云 API 失败: {exc}")
    else:
        en_md = ocr_via_local(pdf, out, args.backend, args.lang)
    log.info("English markdown: %s", en_md)

    if args.skip_translate:
        return

    log.info("[2/2] Translating with %s (workers=%s)", args.model, args.workers)
    md = en_md.read_text(encoding="utf-8")
    chunks = chunk_markdown(md, args.chunk_size)
    log.info("%d chunks", len(chunks))

    client = OpenAI(api_key=api_key, base_url=args.base_url)

    def on_progress(done, total, idx):
        log.info("[%d/%d] chunk #%d ok", done, total, idx + 1)

    translated = translate_chunks_concurrent(
        client, chunks, model=args.model,
        max_workers=args.workers, on_progress=on_progress,
    )

    zh_md = en_md.with_name(en_md.stem + "_zh.md")
    zh_md.write_text("\n\n".join(translated), encoding="utf-8")
    log.info("Done: %s", zh_md)


if __name__ == "__main__":
    main()
