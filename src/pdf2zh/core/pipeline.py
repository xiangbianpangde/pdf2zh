"""PDF → portable English/Chinese Markdown, with optional audit data and HTML.

Default delivery: <out>/<stem>/{<stem>.pdf,<stem>_en.md,<stem>_zh.md,images/}.
OCR caches and translation checkpoints are siblings, never delivery resources.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openai import OpenAI

from . import mineru
from .config import TranslateConfig, resolve_translate
from .html import render_markdown_file
from .contract import (
    canonical_ref, image_link_spans, is_image_file, normalize_image,
    rewrite_image_links, validate_delivery,
)
from .translate import chunk_markdown, clean_reasoning, translate_chunks_concurrent

log = logging.getLogger("pdf2zh.pipeline")
Emit = Callable[[str], None]
Progress = Callable[[int, int, int], None]
MINERU_SIDECARS = ("layout.json", "content_list.json", "middle.json", "model.json")


class PipelineError(RuntimeError):
    """A failed stage; partial output must not be presented as complete."""


def safe_stem(pdf: Path) -> str:
    stem = Path(pdf).stem.strip() or "document"
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", stem)
    return re.sub(r"\s+", " ", stem).strip(" .")[:120] or "document"


def _assert_no_symlink(path: Path, root: Path) -> None:
    """Reject an existing symlink anywhere in a writable path below root."""
    path = Path(os.path.abspath(os.fspath(path)))
    root = Path(os.path.abspath(os.fspath(root)))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PipelineError(f"输出路径越界：{path}") from exc
    current = path
    while True:
        if current.is_symlink():
            raise PipelineError(f"拒绝写入软链接路径：{current}")
        if current == root:
            return
        if current.parent == current:
            raise PipelineError(f"输出根目录不合法：{root}")
        current = current.parent


def _atomic_json(path: Path, payload: dict) -> None:
    _assert_no_symlink(path, path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _options(layout: str, workers: int = 1, chunk_size: int = 1) -> None:
    if layout not in {"minimal", "audit"}:
        raise PipelineError("输出布局必须为 minimal/audit")
    if not 1 <= workers <= 32 or chunk_size < 1:
        raise PipelineError("workers 必须在 1–32 之间，chunk_size 必须为正数")


def _run_fingerprint(source_sha256: str, ocr_settings: str, *, provider: str | None,
                     base_url: str | None, model: str | None, chunk_size: int,
                     skip_translate: bool, layout: str, render_html: bool) -> str:
    payload = {
        "source_sha256": source_sha256,
        "ocr_settings": ocr_settings,
        "provider": provider or "",
        "base_url": base_url or "",
        "model": model or "",
        "chunk_size": chunk_size,
        "skip_translate": skip_translate,
        "layout": layout,
        "render_html": render_html,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _find_markdown(root: Path, stem: str) -> Path:
    for name in ("full.md", "full.txt", f"{stem}.md"):
        if (root / name).is_file():
            return root / name
    matches = sorted(root.rglob("*.md"), key=lambda path: (len(path.parts), str(path)))
    if not matches:
        raise PipelineError(f"解包结果里没有 Markdown：{root}")
    return matches[0]


def _find_images(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if is_image_file(path)),
                  key=lambda path: ("images" not in path.parts[:-1], str(path)))


def ocr(pdf: Path, workdir: Path, mode: str = "precision", model: str = "vlm",
        backend: str = "pipeline", lang: str = "en", timeout: int = 900,
        emit: Emit | None = None, start_page: int | None = None,
        end_page: int | None = None) -> Path:
    say = emit or (lambda _line: None)
    workdir.mkdir(parents=True, exist_ok=True)
    if mode == "local":
        return _ocr_local(pdf, workdir, backend, lang, timeout, say, start_page, end_page)
    if mode == "flash":
        md = mineru.flash_extract(pdf, workdir / "extracted")
    elif mode in {"precision", "api"}:
        md = mineru.precision_extract(pdf, workdir / "extracted", model=model)
    else:
        raise PipelineError(f"未知 OCR 方式：{mode}")
    say(f"  ✓ MinerU {mode} → {md.name}")
    return md.parent


def _ocr_local(pdf: Path, workdir: Path, backend: str, lang: str, timeout: int,
               say: Emit, start_page: int | None = None,
               end_page: int | None = None) -> Path:
    binary = Path(sys.executable).parent / "mineru"
    cmd = [str(binary) if binary.is_file() else "mineru", "-p", str(pdf),
           "-o", str(workdir), "-b", backend, "-l", lang]
    for flag, page in (("-s", start_page), ("-e", end_page)):
        if page is not None and int(page) >= 0:
            cmd.extend([flag, str(int(page))])
    say(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"本地 mineru 超时（>{timeout}s）") from exc
    if proc.returncode:
        raise PipelineError(f"本地 mineru 退出码 {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
    return workdir / pdf.stem


@dataclass
class OrganizeResult:
    out_dir: Path
    en_md: Path
    pdf_copy: Path
    images_dir: Path
    n_images: int
    sidecars: list[Path] = field(default_factory=list)
    manifest_path: Path | None = None
    validation_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _copy_assets(src_md: Path, extracted: Path, images_dir: Path) -> tuple[str, list[dict]]:
    text = src_md.read_text(encoding="utf-8")
    images = _find_images(extracted)
    by_name: dict[str, list[Path]] = {}
    for img in images:
        by_name.setdefault(img.name, []).append(img)
    mapping: dict[str, str] = {}
    manifest: list[dict] = []
    for img in images:
        try:
            dst, item = normalize_image(img, images_dir)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        item["source"] = os.path.relpath(img, extracted).replace(os.sep, "/")
        manifest.append(item)
        relative = dst.relative_to(images_dir.parent).as_posix()
        keys = [str(img), os.path.relpath(img, src_md.parent).replace(os.sep, "/")]
        if len(by_name[img.name]) == 1:
            keys.append(img.name)
        for key in keys:
            mapping[canonical_ref(key)] = relative
    return rewrite_image_links(text, mapping), manifest


def _check(en_md: Path, zh_md: Path | None, images_dir: Path,
           report_path: Path | None = None) -> dict:
    report = validate_delivery(en_md, zh_md, images_dir)
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        codes = ", ".join(sorted({item["code"] for item in report["issues"]}))
        raise PipelineError(f"资源完整性校验失败：{codes}")
    return report


def organize(pdf: Path, extracted: Path, out_dir: Path,
             keep_english: bool = True, *, layout: str = "minimal") -> OrganizeResult:
    """Normalize assets without deleting OCR input or silently guessing filenames."""
    _options(layout)
    pdf, extracted, out_dir = Path(pdf), Path(extracted), Path(out_dir)
    out_root = out_dir.parent
    _assert_no_symlink(out_root, out_root.parent)
    _assert_no_symlink(out_dir, out_root)
    stem = safe_stem(pdf)
    out_dir.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink(out_dir, out_root)
    images_dir = out_dir / "images"
    _assert_no_symlink(images_dir, out_dir)
    images_dir.mkdir(exist_ok=True)
    text, manifest = _copy_assets(_find_markdown(extracted, stem), extracted, images_dir)
    pdf_copy = out_dir / f"{stem}.pdf"
    _assert_no_symlink(pdf_copy, out_dir)
    _assert_no_symlink(out_dir / f"{stem}_en.md", out_dir)
    _assert_no_symlink(out_dir / f"{stem}_zh.md", out_dir)
    if pdf.resolve() != pdf_copy.resolve() and (not pdf_copy.is_file() or _digest(pdf_copy) != _digest(pdf)):
        shutil.copy2(pdf, pdf_copy)
    en_md = out_dir / f"{stem}_en.md"
    en_md.write_text(text, encoding="utf-8")
    sidecars: list[Path] = []
    manifest_path = validation_path = None
    if layout == "audit":
        manifest_path, validation_path = out_dir / "manifest.json", out_dir / "validation.json"
        manifest_path.write_text(json.dumps({"version": 1, "images": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
        if keep_english:
            for name in MINERU_SIDECARS:
                source = next(iter(extracted.rglob(name)), None)
                if source and source.is_file():
                    target = out_dir / "_mineru" / name
                    target.parent.mkdir(exist_ok=True)
                    if source.resolve() != target.resolve():
                        shutil.copy2(source, target)
                    sidecars.append(target)
    report = _check(en_md, None, images_dir, validation_path)
    return OrganizeResult(out_dir, en_md, pdf_copy, images_dir, len(manifest), sidecars,
                          manifest_path, validation_path, [str(v) for v in report["warnings"]])


def _rewrite_image_links(text: str, mapping: dict[str, str]) -> str:
    return rewrite_image_links(text, mapping)


def translate_markdown(en_md: Path, zh_out: Path, cfg: TranslateConfig,
                       chunk_size: int = 3000, workers: int = 8,
                       on_progress: Progress | None = None, emit: Emit | None = None,
                       checkpoint_path: Path | None = None) -> Path:
    say = emit or (lambda _line: None)
    text = en_md.read_text(encoding="utf-8")
    chunks = chunk_markdown(text, chunk_size)
    say(f"  分块 {len(chunks)} 段，并发 {workers}，模型 {cfg.model}")
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=300, max_retries=0)
    checkpoint_path = checkpoint_path or zh_out.with_suffix(zh_out.suffix + ".checkpoint.json")
    parts = translate_chunks_concurrent(
        client, chunks, model=cfg.model, max_workers=workers, on_progress=on_progress,
        checkpoint_path=checkpoint_path,
        checkpoint_context=f"{cfg.base_url}|{cfg.model}|{chunk_size}|{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
    )
    if any(not clean_reasoning(part) and chunk.strip() for part, chunk in zip(parts, chunks)):
        raise PipelineError("翻译返回空内容，检查点保留，未覆盖已有中文")
    temporary_output = zh_out.with_suffix(zh_out.suffix + ".tmp")
    temporary_output.write_text("\n\n".join(clean_reasoning(part) for part in parts), encoding="utf-8")
    _check(en_md, temporary_output, en_md.parent / "images")
    os.replace(temporary_output, zh_out)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    say(f"  ✓ 中文 MD：{zh_out.name}（{zh_out.stat().st_size:,} 字节）")
    return zh_out


@dataclass
class ProcessResult:
    pdf: Path
    out_dir: Path
    zh_md: Path
    en_md: Path
    pdf_copy: Path
    images_dir: Path
    n_images: int
    ocr_mode: str
    translated: bool
    skipped: bool = False
    notes: list[str] = field(default_factory=list)
    html_paths: list[Path] = field(default_factory=list)
    validation: dict | None = None


def _finish(result: ProcessResult, layout: str, render_html: bool) -> ProcessResult:
    report_path = result.out_dir / "validation.json" if layout == "audit" else None
    result.validation = _check(result.en_md, result.zh_md if result.translated else None,
                               result.images_dir, report_path)
    result.notes.extend(str(item) for item in result.validation["warnings"])
    if layout == "audit" and not (result.out_dir / "manifest.json").exists():
        manifest = [normalize_image(p, result.images_dir)[1]
                    for p in sorted(result.images_dir.iterdir()) if p.is_file()]
        (result.out_dir / "manifest.json").write_text(
            json.dumps({"version": 1, "images": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    if render_html:
        result.html_paths.append(render_markdown_file(result.en_md, lang="en"))
        if result.translated:
            result.html_paths.append(render_markdown_file(result.zh_md, lang="zh-CN"))
    return result


def process(pdf: Path, out_root: Path, *, ocr_mode: str = "precision",
            ocr_model: str = "vlm", backend: str = "pipeline", lang: str = "en",
            provider: str | None = None, base_url: str | None = None,
            model: str | None = None, api_key: str | None = None,
            chunk_size: int = 3000, workers: int = 8, keep_english: bool = True,
            skip_translate: bool = False, force: bool = False,
            use_cache: bool = True, timeout: int = 900,
            start_page: int | None = None, end_page: int | None = None,
            layout: str = "minimal", render_html: bool = False,
            emit: Emit | None = None, on_progress: Progress | None = None) -> ProcessResult:
    _options(layout, workers, chunk_size)
    say = emit or (lambda _line: None)
    pdf = Path(pdf).expanduser().resolve()
    out_root = Path(os.path.abspath(os.fspath(Path(out_root).expanduser())))
    if not pdf.is_file():
        raise PipelineError(f"PDF 不存在：{pdf}")
    stem = safe_stem(pdf)
    out_dir = out_root / stem
    en_md, zh_md = out_dir / f"{stem}_en.md", out_dir / f"{stem}_zh.md"
    pdf_copy, images_dir = out_dir / f"{stem}.pdf", out_dir / "images"
    digest = _digest(pdf)
    _assert_no_symlink(out_root, out_root.parent)
    _assert_no_symlink(out_dir, out_root)
    _assert_no_symlink(pdf_copy, out_dir)
    _assert_no_symlink(images_dir, out_dir)
    if pdf_copy.is_file() and _digest(pdf_copy) != digest and not force:
        raise PipelineError(f"同名目录属于不同 PDF：{out_dir}；请更换输出根目录或明确使用 --force")
    audit_ready = layout != "audit" or ((out_dir / "manifest.json").is_file() and (out_dir / "validation.json").is_file())
    html_ready = not render_html or ((out_dir / f"{stem}_en.html").is_file() and (out_dir / f"{stem}_zh.html").is_file())
    completion = out_dir / ".completion.json"
    settings = json.dumps([ocr_mode, ocr_model, backend, lang, start_page, end_page], ensure_ascii=True)
    cfg_probe = None if skip_translate else resolve_translate(provider=provider, base_url=base_url, model=model, api_key=api_key)
    fp_provider = cfg_probe.provider if cfg_probe else provider
    fp_base_url = cfg_probe.base_url if cfg_probe else base_url
    fp_model = cfg_probe.model if cfg_probe else model
    expected_completion = _run_fingerprint(digest, settings, provider=fp_provider, base_url=fp_base_url, model=fp_model, chunk_size=chunk_size, skip_translate=skip_translate, layout=layout, render_html=render_html)
    completion_ok = False
    if completion.is_file():
        try:
            completion_ok = json.loads(completion.read_text(encoding="utf-8")).get("fingerprint") == expected_completion
        except (OSError, ValueError, TypeError):
            completion_ok = False
    if not force and use_cache and not skip_translate and completion_ok and audit_ready and html_ready and pdf_copy.is_file() and images_dir.is_dir() and en_md.is_file() and zh_md.is_file() and zh_md.stat().st_size:
        report = validate_delivery(en_md, zh_md, images_dir)
        if report["ok"]:
            say(f"⏭ 已有完整且资源有效的交付物：{out_dir}")
            result = ProcessResult(pdf, out_dir, zh_md, en_md, pdf_copy, images_dir,
                                   report["image_count"], ocr_mode, True, skipped=True)
            return _finish(result, layout, render_html)
        say("↻ 已有中文 MD，但资源不完整，重新整理")
    cfg = cfg_probe
    cache_key = hashlib.sha256(f"{digest}|{settings}".encode()).hexdigest()
    cache_root = out_root / ".cache"
    _assert_no_symlink(cache_root, out_root)
    cache_dir = cache_root / cache_key
    _assert_no_symlink(cache_dir, cache_root)
    extracted = cache_dir / "extracted"
    receipt = cache_dir / "ocr.json"
    ready = cache_dir / "READY"
    cache_valid = False
    if receipt.is_file() and ready.is_file() and not force:
        try:
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            candidate = cache_dir / saved.get("relative_result", "extracted")
            if candidate.resolve().is_relative_to(cache_dir) and saved.get("source_sha256") == digest and saved.get("settings") == settings:
                extracted = candidate
                cache_valid = True
        except (OSError, ValueError, TypeError, KeyError):
            cache_valid = False
    if use_cache and not force and cache_valid and extracted.is_dir() and any(extracted.rglob("*.md")):
        say("[1/3] OCR：命中内容与参数一致的缓存")
    else:
        # --refresh uses a new run directory; it never destroys the previous cache.
        import uuid
        run_dir = cache_dir / f"run-{uuid.uuid4().hex[:12]}"
        say(f"[1/3] OCR：MinerU {ocr_mode} 解析 {pdf.name}")
        extracted = ocr(pdf, run_dir, mode=ocr_mode, model=ocr_model, backend=backend,
                        lang=lang, timeout=timeout, emit=say, start_page=start_page, end_page=end_page)
        if not extracted.resolve().is_relative_to(cache_dir):
            raise PipelineError("OCR 返回了缓存范围外的路径")
        cache_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(receipt, {"source_sha256": digest, "settings": settings,
                               "relative_result": extracted.relative_to(cache_dir).as_posix()})
        ready.write_text("ok\n", encoding="utf-8")
    say(f"[2/3] 资源归位：{out_dir}")
    org = organize(pdf, extracted, out_dir, keep_english=keep_english, layout=layout)
    result = ProcessResult(pdf, out_dir, zh_md, org.en_md, org.pdf_copy, org.images_dir,
                           org.n_images, ocr_mode, False)
    if skip_translate:
        say("[3/3] 翻译：按参数跳过")
    else:
        say(f"[3/3] 翻译：{cfg.describe()}")
        try:
            checkpoint_path = out_root / ".checkpoints" / f"{stem}-{digest[:20]}.translation.json"
            translate_markdown(org.en_md, zh_md, cfg, chunk_size=chunk_size, workers=workers,
                               emit=say, on_progress=on_progress, checkpoint_path=checkpoint_path)
            result.translated = True
        except Exception as exc:
            result.translated = False
            result.notes.append(f"翻译失败：{exc}")
            say(f"  ✗ {result.notes[-1]}；英文与检查点保留")
            if not keep_english:
                raise
    result = _finish(result, layout, render_html)
    if result.translated:
        _atomic_json(completion, {"fingerprint": expected_completion})
    return result


def process_markdown(md: Path, out_root: Path, *, provider: str | None = None,
                     base_url: str | None = None, model: str | None = None,
                     api_key: str | None = None, chunk_size: int = 3000, workers: int = 8,
                     force: bool = False, skip_translate: bool = False,
                     layout: str = "minimal", render_html: bool = False,
                     emit: Emit | None = None, on_progress: Progress | None = None) -> ProcessResult:
    _options(layout, workers, chunk_size)
    say = emit or (lambda _line: None)
    md = Path(md).expanduser().resolve()
    out_root = Path(os.path.abspath(os.fspath(Path(out_root).expanduser())))
    if not md.is_file():
        raise PipelineError(f"Markdown 不存在：{md}")
    stem = safe_stem(md)
    out_dir = out_root / stem
    _assert_no_symlink(out_root, out_root.parent)
    _assert_no_symlink(out_dir, out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink(out_dir, out_root)
    images_dir = out_dir / "images"
    _assert_no_symlink(images_dir, out_dir)
    images_dir.mkdir(exist_ok=True)
    en_md, zh_md = out_dir / f"{stem}_en.md", out_dir / f"{stem}_zh.md"
    _assert_no_symlink(en_md, out_dir)
    _assert_no_symlink(zh_md, out_dir)
    text, mapping = md.read_text(encoding="utf-8"), {}
    for link in image_link_spans(text):
        from urllib.parse import urlsplit, unquote
        url = urlsplit(link.source)
        if url.scheme in {"http", "https", "data"}:
            continue
        source = (md.parent / unquote(url.path)).resolve()
        if not source.is_relative_to(md.parent) or not source.is_file():
            raise PipelineError(f"Markdown 图片缺失或越界：{link.source}")
        target, _ = normalize_image(source, images_dir)
        mapping[link.source] = target.relative_to(out_dir).as_posix()
    text = rewrite_image_links(text, mapping)
    same_source = en_md.is_file() and en_md.read_text(encoding="utf-8") == text
    completion = out_dir / ".completion.json"
    cfg_probe = None if skip_translate else resolve_translate(provider=provider, base_url=base_url, model=model, api_key=api_key)
    fingerprint = _run_fingerprint(_digest(md), "markdown-only", provider=cfg_probe.provider if cfg_probe else provider,
                                   base_url=cfg_probe.base_url if cfg_probe else base_url,
                                   model=cfg_probe.model if cfg_probe else model,
                                   chunk_size=chunk_size, skip_translate=skip_translate,
                                   layout=layout, render_html=render_html)
    completion_ok = False
    if completion.is_file():
        try:
            completion_ok = json.loads(completion.read_text(encoding="utf-8")).get("fingerprint") == fingerprint
        except (OSError, ValueError, TypeError):
            completion_ok = False
    if same_source and completion_ok and zh_md.is_file() and zh_md.stat().st_size and not force:
        report = validate_delivery(en_md, zh_md, images_dir)
        if report["ok"]:
            return _finish(ProcessResult(md, out_dir, zh_md, en_md, md, images_dir,
                                         report["image_count"], "none", True, skipped=True), layout, render_html)
    en_md.write_text(text, encoding="utf-8")
    _check(en_md, None, images_dir)
    if skip_translate:
        result = ProcessResult(md, out_dir, zh_md, en_md, md, images_dir,
                               len(list(images_dir.iterdir())), "none", False)
        result.notes.append("--skip-translate")
        return _finish(result, layout, render_html)
    cfg = cfg_probe
    result = ProcessResult(md, out_dir, zh_md, en_md, md, images_dir,
                           len(list(images_dir.iterdir())), "none", False)
    try:
        checkpoint = out_root / ".checkpoints" / f"{stem}-{_digest(md)[:20]}.translation.json"
        translate_markdown(en_md, zh_md, cfg, chunk_size, workers, on_progress, say, checkpoint)
        result.translated = True
    except Exception as exc:
        result.notes.append(f"翻译失败：{exc}")
        say(result.notes[-1])
    result = _finish(result, layout, render_html)
    if result.translated:
        _atomic_json(completion, {"fingerprint": fingerprint})
    return result
