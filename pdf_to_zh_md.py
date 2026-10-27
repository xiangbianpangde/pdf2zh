"""英文 PDF → 中文 Markdown 交付目录（MinerU 云 OCR + 本地 MiniMax-M3）。

每个 PDF 产出一个子目录：

    <out>/<pdf 名>/
        <pdf 名>_zh.md      中文 Markdown
        <pdf 名>.pdf        原始 PDF 副本
        images/             OCR 抽取的图片 / 图表
        <pdf 名>_en.md      英文原文（默认保留，便于回溯/重译）
        _mineru/            MinerU 附产物（layout.json 等）

用法：
    python pdf_to_zh_md.py paper.pdf                     # 单份，默认 ./output
    python pdf_to_zh_md.py *.pdf -o ~/Desktop/out         # 批量
    python pdf_to_zh_md.py paper.pdf --ocr flash          # 免 Token 的 flash 模式
    python pdf_to_zh_md.py paper.pdf --skip-translate     # 只做 OCR
    python pdf_to_zh_md.py --doctor                       # 只检查凭据
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from pathlib import Path

import config
import pipeline
from config import ConfigError
from pipeline import PipelineError, safe_stem

log = logging.getLogger("pdf2zh")
DEFAULT_OUT = Path("./output")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="英文 PDF → 中文 Markdown（MinerU 云 OCR + MiniMax 翻译）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("pdfs", type=Path, nargs="*", help="输入英文 PDF（可多个）")
    p.add_argument("-o", "--out-root", type=Path, default=DEFAULT_OUT,
                   help=f"输出根目录，每个 PDF 一个子目录（默认 {DEFAULT_OUT}）")

    g = p.add_argument_group("OCR（MinerU）")
    g.add_argument("--ocr", default="precision",
                   choices=["precision", "flash", "local", "api"],
                   help="precision=云 API v4（默认，需 Token）；flash=云 API 免 Token"
                        "（≤10MB/≤20页）；local=本机 mineru CLI；api 等价于 precision")
    g.add_argument("--ocr-model", default="vlm", choices=["vlm", "pipeline"],
                   help="云端解析模型（默认 vlm，质量优先）")
    g.add_argument("-b", "--backend", default="pipeline",
                   choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                   help="本机 mineru 后端（仅 --ocr local 生效）")
    g.add_argument("-l", "--lang", default="en", help="本机 OCR 语言提示（仅 local）")

    t = p.add_argument_group("翻译")
    t.add_argument("--provider", default=None,
                   help="翻译后端预设（默认 minimax，即本地已配置好的 MiniMax）")
    t.add_argument("--model", default=None, help="模型名（默认取 .env / 预设）")
    t.add_argument("--base-url", default=None, help="API base URL")
    t.add_argument("--api-key", default=None, help="显式 API key")
    t.add_argument("--chunk-size", type=int, default=3000, help="单块最大字符数")
    t.add_argument("--workers", type=int, default=8, help="翻译并发数")
    t.add_argument("--skip-translate", action="store_true", help="只做 OCR，不翻译")

    o = p.add_argument_group("输出与续跑")
    o.add_argument("--layout", choices=["minimal", "audit"], default="minimal",
                   help="输出布局：minimal=pdf+中英 MD+images/（默认）；audit=额外写 manifest/validation/_mineru")
    o.add_argument("--html", dest="render_html", action="store_true",
                   help="额外生成英文/中文 HTML 阅读预览（不等于原 PDF 版式复刻）")
    o.add_argument("--no-html", dest="render_html", action="store_false",
                   help=argparse.SUPPRESS)
    p.set_defaults(render_html=False)
    o.add_argument("--no-keep-english", dest="keep_english", action="store_false",
                   help="不保留英文原文与审计附产物（默认保留）")
    o.add_argument("--force", action="store_true", help="中文 MD 已存在也重做")
    o.add_argument("--refresh", action="store_true",
                   help="忽略 OCR 缓存，强制重新调用 MinerU 云 API")
    o.add_argument("--timeout", type=int, default=900, help="单份 OCR 超时秒数")
    o.add_argument("--parallelism", type=int, default=1,
                   help="同时处理的 PDF 数（默认 1；云 OCR/翻译建议从 2 开始）")

    p.add_argument("--doctor", action="store_true", help="只检查凭据配置后退出")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    return p


def run_doctor() -> int:
    print("=" * 72)
    print("pdf2zh 凭据自检")
    print("=" * 72)
    bad: list[str] = []
    for ok, required, msg in config.doctor():
        mark = "✓" if ok else ("✗" if required else "·")
        print(f"  {mark} {msg}")
        if required and not ok:
            bad.append(msg.split(":")[0].strip())
    print("-" * 72)
    print(f"  .env     : {config.ENV_FILE} "
          f"{'（存在）' if config.ENV_FILE.exists() else '（不存在，用内置预设）'}")
    print(f"  .secrets : {config.SECRETS_DIR}")
    if bad:
        print(f"  ✗ 默认管线缺 {len(bad)} 项：{'、'.join(bad)}")
        return 1
    print(f"  ✓ 默认管线（{config.DEFAULT_PROVIDER} + MinerU 云 OCR）凭据齐备"
          "，非默认后端的缺项不影响使用")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if not args.verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("pdf2zh.pipeline").setLevel(logging.INFO)

    if args.doctor:
        return run_doctor()
    if not args.pdfs:
        build_parser().print_help()
        print("\n[FATAL] 请至少给一个 PDF，或用 --doctor 检查配置", file=sys.stderr)
        return 2

    if args.ocr == "api":
        args.ocr = "precision"

    missing = [p for p in args.pdfs if not p.is_file()]
    if missing:
        print(f"[FATAL] PDF 不存在：{', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 2

    stems = [safe_stem(pdf) for pdf in args.pdfs]
    duplicates = sorted({stem for stem in stems if stems.count(stem) > 1})
    if duplicates:
        print(f"[FATAL] 输入文件名映射到相同输出目录：{', '.join(duplicates)}", file=sys.stderr)
        return 2

    out_root = args.out_root.expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"输出根目录：{out_root}")
    print(f"共 {len(args.pdfs)} 份 PDF\n" + "=" * 68)

    if args.parallelism <= 0 or args.parallelism > 16:
        print("[FATAL] --parallelism 必须在 1 到 16 之间", file=sys.stderr)
        return 2
    t_all = time.time()
    ok, failed, skipped = 0, 0, 0

    def process_one(item: tuple[int, Path]):
        i, pdf = item
        def emit(line: str) -> None:
            print(f"[{i}/{len(args.pdfs)}] {pdf.name}: {line}", flush=True)
        def on_progress(done: int, total: int, idx: int) -> None:
            print(f"[{i}/{len(args.pdfs)}] {pdf.name}: [{done}/{total}] 段落 #{idx + 1} 完成", flush=True)
        started = time.time()
        try:
            result = pipeline.process(
                pdf, out_root, ocr_mode=args.ocr, ocr_model=args.ocr_model,
                backend=args.backend, lang=args.lang, provider=args.provider,
                base_url=args.base_url, model=args.model, api_key=args.api_key,
                chunk_size=args.chunk_size, workers=args.workers,
                keep_english=args.keep_english, skip_translate=args.skip_translate,
                force=args.force, use_cache=not args.refresh, timeout=args.timeout,
                layout=args.layout, render_html=args.render_html,
                emit=emit, on_progress=on_progress)
            return i, pdf, result, None, time.time() - started
        except Exception as exc:
            return i, pdf, None, exc, time.time() - started

    try:
        with ThreadPoolExecutor(max_workers=min(args.parallelism, len(args.pdfs))) as pool:
            futures = [pool.submit(process_one, (i, pdf)) for i, pdf in enumerate(args.pdfs, 1)]
            for future in as_completed(futures):
                i, pdf, res, error, elapsed = future.result()
                print(f"\n=== [{i}/{len(args.pdfs)}] {pdf.name} ===")
                if error:
                    print(f"  ✗ 失败：{error}", file=sys.stderr)
                    failed += 1
                    continue
                if res.skipped:
                    skipped += 1
                elif res.translated or args.skip_translate:
                    ok += 1
                else:
                    failed += 1
                print(f"  — {elapsed:.1f}s｜目录 {res.out_dir.name}/"
                      f"｜图片 {res.n_images} 张｜{'中文MD已生成' if res.translated else '仅OCR'}")
    except KeyboardInterrupt:
        print("\n已中断（已完成的 PDF 与翻译分块检查点保留，下次用相同参数重跑）", file=sys.stderr)
        return 130

    print("\n" + "=" * 68)
    print(f"完成 {ok}｜跳过 {skipped}｜失败 {failed}｜"
          f"总耗时 {(time.time() - t_all) / 60:.1f} min")
    print(f"产物目录：{out_root}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
