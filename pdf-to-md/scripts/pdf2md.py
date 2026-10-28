#!/usr/bin/env python3
"""PDF → Markdown delivery CLI.

One PDF in, one portable directory out::

    <out>/<stem>/
    ├── <stem>.md            MinerU Markdown, links rewritten to images/
    ├── <stem>_zh.md         Chinese translation (only with --translate)
    ├── <stem>.pdf           copy of the source
    ├── images/              normalized figures
    └── _meta/               manifest, validation, MinerU structure JSON

Every translation endpoint parameter is a flag, so switching models never needs a
code change::

    --base-url https://api.example.com/v1 --model some-model --api-key sk-...

Exit codes: 0 success, 1 stage failure, 2 usage or configuration error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import config
import mineru_client
import organize
import translate as translation
import validate as validation
from errors import ConfigError, Pdf2mdError, UsageError

STAGE_OCR = "[1/3] OCR"
STAGE_ORGANIZE = "[2/3] 归位"
STAGE_TRANSLATE = "[3/3] 翻译"


@dataclass
class Options:
    ocr: str = "precision"
    ocr_model: str = "vlm"
    language: str = "en"
    pages: str | None = None
    translate: bool = False
    target: str = "zh"
    chunk_size: int = 3000
    workers: int = 8
    out_root: Path | None = None
    force: bool = False
    timeout: float = 1800.0
    quiet: bool = False
    offline: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Result:
    pdf: Path
    out_dir: Path
    markdown: Path
    chinese: Path | None
    images: int
    channel: str
    translated: bool
    skipped: bool = False
    validation: dict | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pdf": str(self.pdf),
            "out_dir": str(self.out_dir),
            "markdown": str(self.markdown),
            "chinese": str(self.chinese) if self.chinese else None,
            "images": self.images,
            "channel": self.channel,
            "translated": self.translated,
            "skipped": self.skipped,
            "validation": self.validation,
            "notes": self.notes,
        }


class Printer:
    """Console reporter. Quiet mode keeps only errors."""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def report(self, message: str) -> None:
        self.info(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="PDF → Markdown（MinerU 云解析 + 可选模型翻译）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  pdf2md.py paper.pdf\n"
            "  pdf2md.py paper.pdf --translate --base-url https://api.example.com/v1 \\\n"
            "           --model some-model --api-key sk-...\n"
            "  pdf2md.py paper.pdf --ocr flash --translate   # 无 Token，纯文本\n"
            "  pdf2md.py --doctor\n"
        ),
    )
    parser.add_argument("pdfs", nargs="*", type=Path, help="输入 PDF（可多个）")
    parser.add_argument("-o", "--out-root", type=Path, default=None,
                        help="输出根目录；默认与源 PDF 同级")

    ocr = parser.add_argument_group("OCR（MinerU）")
    ocr.add_argument("--ocr", choices=["precision", "flash"], default="precision",
                     help="precision=需 Token，含图片与结构 JSON（默认）；flash=免 Token，仅 Markdown")
    ocr.add_argument("--ocr-model", choices=["vlm", "pipeline"], default="vlm",
                     help="云端解析模型（默认 vlm）")
    ocr.add_argument("--language", default="en",
                     help="语言提示，默认 en；中文文档用 ch（见 references/mineru-api.md）")
    ocr.add_argument("--pages", default=None,
                     help="页码范围；precision 用逗号语法（1,4-6），flash 只支持 1-10 或 5")
    ocr.add_argument("--mineru-token", default=None, help="显式 MinerU Token")

    tr = parser.add_argument_group("翻译（可选）")
    tr.add_argument("--translate", action="store_true", help="生成中文 Markdown")
    tr.add_argument("--target", default="zh", choices=["zh"], help="目标语言（目前仅 zh）")
    tr.add_argument("--base-url", default=None, help="OpenAI 兼容 base URL（含 /v1）")
    tr.add_argument("--model", default=None, help="翻译模型名")
    tr.add_argument("--api-key", default=None, help="翻译 API Key")
    tr.add_argument("--api-header", action="append", default=[], metavar="NAME:VALUE",
                    help="追加请求头，可重复；用于特殊网关")
    tr.add_argument("--chunk-size", type=int, default=3000, help="单块最大字符数")
    tr.add_argument("--workers", type=int, default=8, help="翻译并发数")

    run = parser.add_argument_group("运行")
    run.add_argument("--force", action="store_true", help="忽略已完成标记与 OCR 缓存，全部重做")
    run.add_argument("--timeout", type=float, default=1800.0, help="单份 OCR 等待秒数")
    run.add_argument("--parallelism", type=int, default=1, help="同时处理的 PDF 数")
    run.add_argument("--json", action="store_true", help="结束打印机器可读 JSON 摘要")
    run.add_argument("-q", "--quiet", action="store_true", help="只打印错误")
    run.add_argument("--doctor", action="store_true", help="只做配置与连通性自检")
    run.add_argument("--offline", action="store_true", help="doctor 不做网络探测")
    return parser


def _fingerprint(source_sha: str, *, channel: str, ocr_model: str, language: str,
                 pages: str | None, translate_on: bool, target: str) -> str:
    payload = json.dumps({
        "source": source_sha, "channel": channel, "ocr_model": ocr_model,
        "language": language, "pages": pages,
        "translate": translate_on, "target": target,
    }, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ocr_params(options: Options) -> dict:
    return {"channel": options.ocr, "ocr_model": options.ocr_model,
            "language": options.language, "pages": options.pages}


@dataclass(frozen=True)
class Cached:
    root: Path


def _cache_dir(delivery: organize.Delivery, params: dict, source_sha: str) -> Path:
    key = hashlib.sha256(json.dumps({"source": source_sha, **params},
                                    sort_keys=True).encode()).hexdigest()[:32]
    return delivery.cache_root / key


def _cache_lookup(delivery: organize.Delivery, params: dict, source_sha: str,
                  options: Options) -> Path | None:
    if options.force:
        return None
    directory = _cache_dir(delivery, params, source_sha)
    receipt = directory / "receipt.json"
    ready = directory / "READY"
    if not (receipt.is_file() and ready.is_file()):
        return None
    try:
        saved = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if saved.get("source") != source_sha or saved.get("params") != params:
        return None
    relative = str(saved.get("relative", "extracted"))
    candidate = (directory / relative).resolve()
    if directory.resolve() not in candidate.parents and candidate != directory.resolve():
        return None
    return candidate if candidate.is_dir() else None


def _cache_store(delivery: organize.Delivery, params: dict, source_sha: str,
                 extracted: Path) -> Path:
    directory = _cache_dir(delivery, params, source_sha)
    directory.mkdir(parents=True, exist_ok=True)
    relative = extracted.resolve().relative_to(directory.resolve())
    organize.atomic_json(directory / "receipt.json",
                         {"source": source_sha, "params": params,
                          "relative": relative.as_posix(), "created": int(time.time())})
    (directory / "READY").write_text("ok\n", encoding="utf-8")
    return directory


def _prune_empty(path: Path, stop: Path) -> None:
    """Remove empty directories left by a failed stage, never touching parents beyond ``stop``."""
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _complete(delivery: organize.Delivery, fingerprint: str, *, translate_on: bool) -> bool:
    if not delivery.completion.is_file():
        return False
    try:
        saved = json.loads(delivery.completion.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if saved.get("fingerprint") != fingerprint:
        return False
    if not delivery.markdown.is_file() or not delivery.pdf_copy.is_file():
        return False
    if translate_on and not (delivery.chinese.is_file() and delivery.chinese.stat().st_size):
        return False
    report = validation.validate(delivery.markdown,
                                 delivery.chinese if translate_on else None,
                                 delivery.images_dir)
    return report.ok


def _endpoint(settings: config.Settings, options: Options) -> translation.Endpoint:
    base_url, model, api_key = settings.translation()
    headers: dict[str, str] = dict(options.extra_headers)
    return translation.Endpoint(base_url=base_url.text, model=model.text,
                                api_key=api_key.text, timeout=300.0,
                                extra_headers=headers)


def process_pdf(pdf: Path, options: Options, settings: config.Settings,
                printer: Printer) -> Result:
    source = pdf.expanduser()
    if not source.is_file():
        raise UsageError(f"PDF 不存在：{source}")
    if source.stat().st_size == 0:
        raise UsageError(f"PDF 是空文件：{source}")

    stem = organize.safe_stem(source)
    out_root = options.out_root or source.parent
    delivery = organize.delivery_paths(out_root, stem)
    source_sha = organize.sha256_file(source)
    if delivery.pdf_copy.is_file() and organize.sha256_file(delivery.pdf_copy) != source_sha and not options.force:
        raise Pdf2mdError(
            f"输出目录已属于另一份 PDF：{delivery.out_dir}\n"
            "请换一个输出根目录，或加 --force 覆盖。")

    fingerprint = _fingerprint(source_sha, channel=options.ocr,
                               ocr_model=options.ocr_model, language=options.language,
                               pages=options.pages, translate_on=options.translate,
                               target=options.target)
    if _complete(delivery, fingerprint, translate_on=options.translate):
        report = validation.validate(delivery.markdown,
                                     delivery.chinese if options.translate else None,
                                     delivery.images_dir)
        printer.info(f"⏭ 已有有效交付：{delivery.out_dir}")
        return Result(pdf=source, out_dir=delivery.out_dir, markdown=delivery.markdown,
                      chinese=delivery.chinese if options.translate else None,
                      images=report.image_count, channel=options.ocr,
                      translated=options.translate, skipped=True,
                      validation=report.as_dict())

    params = _ocr_params(options)
    notes: list[str] = []
    printer.info(f"{STAGE_OCR}：{source.name}（{source.stat().st_size / 1048576:.1f} MB）")
    cached = _cache_lookup(delivery, params, source_sha, options)
    if cached is not None:
        printer.info(f"  命中 OCR 缓存：{cached}")
        extracted, channel = cached, options.ocr
        content_list = next((path for path in extracted.rglob("*content_list*.json")), None)
    else:
        run_dir = _cache_dir(delivery, params, source_sha) / "runs" / f"{int(time.time())}-{os.urandom(4).hex()}"
        run_dir.mkdir(parents=True, exist_ok=True)
        if options.ocr == "flash":
            try:
                parsed = mineru_client.flash_parse(
                    source, run_dir, language=options.language, pages=options.pages,
                    timeout=options.timeout, report=printer.report)
            except Exception:
                _prune_empty(run_dir, delivery.out_root)
                raise
        else:
            token = settings.require_mineru()
            try:
                parsed = mineru_client.precision_parse(
                    source, run_dir, token.text, model=options.ocr_model,
                    language=options.language, pages=options.pages,
                    timeout=options.timeout, report=printer.report)
            except Exception:
                _prune_empty(run_dir, delivery.out_root)
                raise
        extracted, channel = parsed.root, parsed.channel
        content_list = parsed.content_list
        _cache_store(delivery, params, source_sha, extracted)
        printer.info(f"  解析完成：{parsed.markdown.name}")
        if channel == "flash":
            printer.info("  ⚠ flash 通道只返回 Markdown：本次没有图片、没有结构 JSON")
            notes.append("flash 通道不返回图片与结构 JSON")

    printer.info(f"{STAGE_ORGANIZE}：{delivery.out_dir}")
    organized = organize.organize(source, extracted, delivery, channel=channel,
                                  params=params, content_list=content_list)
    report = validation.validate(organized.markdown, None, delivery.images_dir)
    if not report.ok:
        raise Pdf2mdError(f"图片资源校验失败：{report.summary()}")
    printer.info(f"  {organized.markdown.name}｜图片 {len(organized.images)} 张｜{report.summary()}")

    chinese: Path | None = None
    if options.translate:
        endpoint = _endpoint(settings, options)
        printer.info(f"{STAGE_TRANSLATE}：{endpoint.describe()}")
        client = translation.Client(endpoint)
        text = organized.markdown.read_text(encoding="utf-8")
        context = hashlib.sha256(
            f"{endpoint.base_url}|{endpoint.model}|{options.chunk_size}|{source_sha}".encode()
        ).hexdigest()
        checkpoint = translation.Checkpoint(
            delivery.checkpoint_dir / f"{stem}-{context[:20]}.json")
        translated = translation.translate_text(
            text, client, chunk_size=options.chunk_size, workers=options.workers,
            checkpoint=checkpoint, context=context,
            on_progress=lambda done, total, index:
                printer.info(f"  [{done}/{total}] 段落 #{index + 1} 完成"),
            report=printer.report)
        temporary = delivery.chinese.with_name(f".{delivery.chinese.name}.tmp")
        organize.atomic_text(temporary, translated, root=delivery.out_dir)
        bilingual = validation.validate(organized.markdown, temporary, delivery.images_dir)
        if not bilingual.ok:
            temporary.unlink(missing_ok=True)
            raise Pdf2mdError(f"中英资源校验失败：{bilingual.summary()}（中文文件未写入）")
        os.replace(temporary, delivery.chinese)
        checkpoint.clear()
        chinese = delivery.chinese
        printer.info(f"  {chinese.name}（{chinese.stat().st_size:,} 字节）")

    final = validation.validate(organized.markdown, chinese, delivery.images_dir)
    organize.atomic_json(delivery.validation, final.as_dict(), root=delivery.out_dir)
    if final.ok:
        organize.atomic_json(delivery.completion,
                             {"fingerprint": fingerprint, "finished": int(time.time())},
                             root=delivery.out_dir)
    result = Result(pdf=source, out_dir=delivery.out_dir, markdown=organized.markdown,
                    chinese=chinese, images=final.image_count, channel=channel,
                    translated=bool(chinese), validation=final.as_dict(), notes=notes)
    printer.info(f"  ✓ {delivery.out_dir}")
    return result


def run_doctor(settings: config.Settings, options: Options) -> int:
    print("=" * 72)
    print("pdf2md 自检")
    print("=" * 72)
    failures = 0
    for state, label, detail in settings.describe():
        mark = "✓" if state == "ok" else "·"
        print(f"  {mark} {label:14s} {detail}")
    print("-" * 72)
    if settings.mineru_token is None:
        print("  ✗ MinerU Token 缺失：precision 通道不可用（图片无法保留）")
        failures += 1
    elif options.offline:
        print("  · MinerU Token 已配置（--offline，未探测）")
    else:
        ok, detail = mineru_client.check_token(settings.mineru_token.text)
        print(f"  {'✓' if ok else '✗'} MinerU Token 探测：{detail}")
        failures += 0 if ok else 1

    if all(getattr(settings, field) is not None for field in config.TRANSLATION_FIELDS):
        if options.offline:
            print("  · 翻译端点已配置（--offline，未探测）")
        else:
            try:
                endpoint = _endpoint(settings, options)
                reply = translation.Client(endpoint).complete("只回复两个字：可用")
                print(f"  ✓ 翻译端点探测：{endpoint.describe()} → {reply[:40]!r}")
            except (Pdf2mdError, ConnectionError, ValueError) as exc:
                print(f"  ✗ 翻译端点探测失败：{exc}")
                failures += 1
    else:
        print("  · 翻译端点未配置完整（--translate 时才会用到）")
    print("-" * 72)
    print("  结果：" + ("全部就绪" if failures == 0 else f"{failures} 项需要处理"))
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    printer = Printer(args.quiet)

    try:
        headers = [translation.parse_header(raw) for raw in args.api_header]
    except ValueError as exc:
        printer.error(f"[FATAL] {exc}")
        return 2
    options = Options(ocr=args.ocr, ocr_model=args.ocr_model, language=args.language,
                      pages=args.pages, translate=args.translate, target=args.target,
                      chunk_size=args.chunk_size, workers=args.workers,
                      out_root=args.out_root, force=args.force, timeout=args.timeout,
                      quiet=args.quiet, offline=args.offline,
                      extra_headers=dict(headers))
    if options.workers < 1 or options.workers > 32:
        printer.error("[FATAL] --workers 必须在 1 到 32 之间")
        return 2

    settings = config.load_settings(mineru_token=args.mineru_token, base_url=args.base_url,
                                    model=args.model, api_key=args.api_key)
    if args.doctor:
        return run_doctor(settings, options)

    if not args.pdfs:
        parser.print_help()
        printer.error("\n[FATAL] 至少给一个 PDF，或用 --doctor 自检")
        return 2

    stems: dict[str, Path] = {}
    for pdf in args.pdfs:
        source = pdf.expanduser()
        if not source.is_file():
            printer.error(f"[FATAL] PDF 不存在：{source}")
            return 2
        if source.stat().st_size == 0:
            printer.error(f"[FATAL] PDF 是空文件：{source}")
            return 2
        stem = organize.safe_stem(source)
        if stem in stems:
            printer.error(f"[FATAL] 输入文件映射到同一输出目录：{stems[stem].name} / {source.name}")
            return 2
        stems[stem] = source

    if options.translate:
        try:
            settings.translation()
        except ConfigError as exc:
            printer.error(f"[FATAL] {exc}")
            return 2
    if options.ocr == "precision":
        try:
            settings.require_mineru()
        except ConfigError as exc:
            printer.error(f"[FATAL] {exc}")
            return 2

    parallelism = max(1, min(args.parallelism, 16, len(args.pdfs)))
    started = time.time()
    results: list[Result] = []
    failures: list[tuple[Path, str]] = []

    def job(pdf: Path) -> Result:
        return process_pdf(pdf, options, settings, printer)

    if parallelism == 1:
        for pdf in args.pdfs:
            try:
                results.append(job(pdf))
            except (Pdf2mdError, OSError) as exc:
                failures.append((pdf, str(exc)))
                printer.error(f"[FAIL] {pdf.name}：{exc}")
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            futures = {pool.submit(job, pdf): pdf for pdf in args.pdfs}
            for future in as_completed(futures):
                pdf = futures[future]
                try:
                    results.append(future.result())
                except (Pdf2mdError, OSError) as exc:
                    failures.append((pdf, str(exc)))
                    printer.error(f"[FAIL] {pdf.name}：{exc}")

    elapsed = time.time() - started
    if not args.quiet or failures:
        print("-" * 72)
        print(f"完成 {len(results)}｜失败 {len(failures)}｜耗时 {elapsed:.1f}s")
    if args.json:
        print(json.dumps({
            "ok": not failures,
            "elapsed_seconds": round(elapsed, 1),
            "results": [result.as_dict() for result in results],
            "failures": [{"pdf": str(pdf), "error": error} for pdf, error in failures],
        }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
