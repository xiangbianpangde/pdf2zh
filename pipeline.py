"""pdf2zh 规范管线：英文 PDF → 一个自包含的中文 Markdown 交付目录。

交付格式（每个 PDF 一个子目录）
------------------------------
    <out_root>/<pdf 名>/
        <pdf 名>_zh.md      ← 中文 Markdown（主交付物）
        <pdf 名>.pdf        ← 原始 PDF 副本
        images/             ← MinerU 抽取的图片 / 图表
        （可选 --keep-english）<pdf 名>_en.md + _mineru/{layout,content_list}.json

三个阶段
--------
1. OCR     MinerU **云 API**（默认 v4 Precision，Token 鉴权；`--ocr flash` 免 Token；
           `--ocr local` 回退本机 mineru CLI）
2. ORGANIZE 解包产物归位：图片搬进 images/、PDF 复制进来、图片链接重写为相对路径
3. TRANSLATE 本地配置的 MiniMax-M3（凭据/端点由 config.py 解析，不依赖环境变量残留）

断点续跑：OCR 原始产物缓存在 <out_root>/.cache/<pdf 名>/，重跑不重复消耗 MinerU 配额；
中文 MD 已存在则整份跳过（`--force` 强制重做）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from openai import OpenAI

import mineru_api
from config import TranslateConfig, resolve_translate
from translate_md import chunk_markdown, clean_reasoning, translate_chunks_concurrent

log = logging.getLogger("pdf2zh.pipeline")

Emit = Callable[[str], None]          # 日志回调：(line) -> None
Progress = Callable[[int, int, int], None]  # (completed, total, chunk_index)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
MINERU_SIDECARS = ("layout.json", "content_list.json", "middle.json", "model.json")

# ![alt](src) 与 <img src="...">
_MD_IMG_RE = re.compile(r"(!\[[^\]]*\]\()([^\s)]+)(\))")
_HTML_IMG_RE = re.compile(r"(<img\b[^>]*?\bsrc=)([\"'])([^\"']+)\2", re.IGNORECASE)


class PipelineError(RuntimeError):
    """管线任一阶段失败。"""


# ────────────────────────────── 路径与工具 ──────────────────────────────

def safe_stem(pdf: Path) -> str:
    """把 PDF 文件名变成安全的目录名（macOS/Linux 差异字符统一替换）。"""
    stem = pdf.stem.strip() or "document"
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem[:120] or "document"


def _find_markdown(root: Path, stem: str) -> Path:
    """在解包目录里定位英文 Markdown：优先 full.md，其次与 PDF 同名，再次最近的 .md。"""
    for name in ("full.md", "full.txt"):
        p = root / name
        if p.is_file():
            return p
    direct = sorted(root.glob("*.md"))
    if direct:
        for p in direct:
            if p.stem == stem:
                return p
        return direct[0]
    nested = sorted(p for p in root.rglob("*.md") if p.is_file())
    if not nested:
        raise PipelineError(f"解包结果里没有 Markdown：{root}")
    for p in nested:
        if p.stem == stem:
            return p
    return min(nested, key=lambda p: len(p.parts))


def _find_images(root: Path) -> list[Path]:
    """收集解包目录里的全部图片（任意深度）。

    排序把 images/ 目录下的排在前面：它们拿到干净的文件名，
    散落在别处的同名文件再自动加前缀，避免互相覆盖。
    """
    def rank(f: Path) -> tuple[int, str]:
        in_images_dir = any(part.lower() == "images" for part in f.parts[:-1])
        return (0 if in_images_dir else 1, str(f))

    # 不在这里按文件名去重：同名文件交由 organize() 加前缀区分，否则会丢图
    return sorted((f for f in root.rglob("*")
                   if f.is_file() and f.suffix.lower() in IMAGE_EXTS), key=rank)


# ────────────────────────────── 阶段 1：OCR ──────────────────────────────

def ocr(pdf: Path, workdir: Path, mode: str = "precision",
        model: str = "vlm", backend: str = "pipeline",
        lang: str = "en", timeout: int = 900, emit: Emit | None = None,
        start_page: int | None = None, end_page: int | None = None) -> Path:
    """阶段 1：把 PDF 解析成 Markdown + 图片，返回含 full.md 的解包目录。

    mode: precision（云 API，Token）/ flash（云 API，免 Token）/ local（本机 CLI）
    页码范围仅本机 CLI 支持；云端接口按整篇解析。
    """
    say = emit or (lambda _line: None)
    workdir.mkdir(parents=True, exist_ok=True)
    if mode == "local":
        return _ocr_local(pdf, workdir, backend, lang, timeout, say,
                          start_page, end_page)
    if mode == "flash":
        md = mineru_api.flash_extract(pdf, workdir / "extracted")
        say(f"  ✓ MinerU flash（免 Token）→ {md.name}")
        return md.parent
    md = mineru_api.precision_extract(pdf, workdir / "extracted", model=model)
    say(f"  ✓ MinerU 云 API v4 Precision（{model}）→ {md.name}")
    return md.parent


def _ocr_local(pdf: Path, workdir: Path, backend: str, lang: str,
               timeout: int, say: Emit, start_page: int | None = None,
               end_page: int | None = None) -> Path:
    """回退路径：本机 mineru CLI（需已装好本地模型）。"""
    mineru_bin = str(Path(sys.executable).parent / "mineru")
    if not Path(mineru_bin).exists():
        mineru_bin = "mineru"
    cmd = [mineru_bin, "-p", str(pdf), "-o", str(workdir), "-b", backend, "-l", lang]
    if start_page is not None and int(start_page) >= 0:
        cmd += ["-s", str(int(start_page))]
    if end_page is not None and int(end_page) >= 0:
        cmd += ["-e", str(int(end_page))]
    say(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"本地 mineru 超时（>{timeout}s）") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        raise PipelineError(f"本地 mineru 退出码 {proc.returncode}: {tail}")
    return workdir / pdf.stem  # 本地 CLI 产物在 <out>/<stem>/ 下


# ────────────────────────────── 阶段 2：产物归位 ──────────────────────────────

@dataclass
class OrganizeResult:
    out_dir: Path
    en_md: Path
    pdf_copy: Path
    images_dir: Path
    n_images: int
    sidecars: list[Path] = field(default_factory=list)


def organize(pdf: Path, extracted: Path, out_dir: Path,
             keep_english: bool = True) -> OrganizeResult:
    """阶段 2：把解包产物整理成 <out_dir>/{_en.md, .pdf, images/}。"""
    stem = safe_stem(pdf)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)

    src_md = _find_markdown(extracted, stem)
    text = src_md.read_text(encoding="utf-8", errors="replace")

    # 2.1 图片归位 + 建立「原引用 → images/<新名>」映射
    mapping: dict[str, str] = {}
    used: set[str] = set()
    n_images = 0
    for img in _find_images(extracted):
        name = img.name
        if name in used:                      # 同名冲突：加父目录前缀
            name = f"{img.parent.name}_{name}"
            n = 2
            while name in used:
                name = f"{img.parent.name}_{n}_{img.name}"
                n += 1
        used.add(name)
        dst = images_dir / name
        if not dst.exists() or dst.stat().st_size != img.stat().st_size:
            shutil.copy2(img, dst)
        n_images += 1
        for key in (img.name, str(img), _rel_to(src_md.parent, img)):
            mapping.setdefault(key, f"images/{name}")

    # 2.2 原始 PDF 副本
    pdf_copy = out_dir / f"{stem}.pdf"
    if not pdf_copy.exists() or pdf_copy.stat().st_size != pdf.stat().st_size:
        shutil.copy2(pdf, pdf_copy)

    # 2.3 英文 MD（重写图片链接后落位）
    en_md = out_dir / f"{stem}_en.md"
    en_md.write_text(_rewrite_image_links(text, mapping), encoding="utf-8")

    # 2.4 MinerU 附产物（layout.json 等）留档
    sidecars: list[Path] = []
    if keep_english:
        meta_dir = out_dir / "_mineru"
        for name in MINERU_SIDECARS:
            src = next(iter(extracted.rglob(name)), None)
            if src and src.is_file():
                meta_dir.mkdir(exist_ok=True)
                dst = meta_dir / name
                shutil.copy2(src, dst)
                sidecars.append(dst)

    # 2.5 解包目录只作为中间态，默认清掉（缓存由调用方决定）
    if extracted != out_dir and not _is_inside(extracted, out_dir):
        shutil.rmtree(extracted, ignore_errors=True)

    return OrganizeResult(out_dir=out_dir, en_md=en_md, pdf_copy=pdf_copy,
                          images_dir=images_dir, n_images=n_images,
                          sidecars=sidecars)


def _rel_to(base: Path, target: Path) -> str:
    try:
        return str(target.relative_to(base))
    except ValueError:
        return str(target)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _rewrite_image_links(text: str, mapping: dict[str, str]) -> str:
    """把 MD/HTML 里的图片引用改写成 images/<文件名>，让 MD 能在新目录里正常显示。"""
    if not mapping:
        return text
    by_name = {k.rsplit("/", 1)[-1]: v for k, v in mapping.items()}

    def fix(src: str) -> str:
        src = src.strip()
        if not src or src.startswith(("http://", "https://", "data:")):
            return src
        new = mapping.get(src) or mapping.get(src.lstrip("./")) or by_name.get(
            src.rsplit("/", 1)[-1])
        return new or src

    text = _MD_IMG_RE.sub(lambda m: f"{m.group(1)}{fix(m.group(2))}{m.group(3)}", text)
    text = _HTML_IMG_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{fix(m.group(3))}{m.group(2)}", text)
    return text


# ────────────────────────────── 阶段 3：翻译 ──────────────────────────────

def translate_markdown(en_md: Path, zh_out: Path, cfg: TranslateConfig,
                       chunk_size: int = 3000, workers: int = 8,
                       on_progress: Progress | None = None,
                       emit: Emit | None = None) -> Path:
    """阶段 3：英文 MD → 中文 MD。失败时保留英文原文，不覆盖已有中文。"""
    say = emit or (lambda _line: None)
    text = en_md.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(text, chunk_size)
    say(f"  分块 {len(chunks)} 段（每段≤{chunk_size} 字符），并发 {workers}，"
        f"模型 {cfg.model}")
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=300,
                    max_retries=0)
    parts = translate_chunks_concurrent(client, chunks, model=cfg.model,
                                        max_workers=workers, on_progress=on_progress)
    cleaned = [clean_reasoning(p) for p in parts]
    zh_out.write_text("\n\n".join(cleaned), encoding="utf-8")
    say(f"  ✓ 中文 MD：{zh_out.name}（{zh_out.stat().st_size:,} 字节）")
    return zh_out


# ────────────────────────────── 串起来 ──────────────────────────────

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


def process(pdf: Path, out_root: Path, *, ocr_mode: str = "precision",
            ocr_model: str = "vlm", backend: str = "pipeline", lang: str = "en",
            provider: str | None = None, base_url: str | None = None,
            model: str | None = None, api_key: str | None = None,
            chunk_size: int = 3000, workers: int = 8, keep_english: bool = True,
            skip_translate: bool = False, force: bool = False,
            use_cache: bool = True, timeout: int = 900,
            start_page: int | None = None, end_page: int | None = None,
            emit: Emit | None = None,
            on_progress: Progress | None = None) -> ProcessResult:
    """跑完整条管线：云 OCR → 归位 → MiniMax 翻译，返回产物路径。"""
    say = emit or (lambda _line: None)
    pdf = Path(pdf)
    if not pdf.is_file():
        raise PipelineError(f"PDF 不存在：{pdf}")
    out_root = Path(out_root)
    stem = safe_stem(pdf)
    out_dir = out_root / stem
    zh_md = out_dir / f"{stem}_zh.md"
    en_md = out_dir / f"{stem}_en.md"
    pdf_copy = out_dir / f"{stem}.pdf"
    images_dir = out_dir / "images"

    # 断点续跑：中文 MD 已在 → 整份跳过
    if zh_md.exists() and zh_md.stat().st_size > 0 and not force:
        say(f"⏭  已存在中文 MD，跳过：{zh_md}")
        return ProcessResult(
            pdf=pdf, out_dir=out_dir, zh_md=zh_md,
            en_md=en_md if en_md.exists() else zh_md,
            pdf_copy=pdf_copy if pdf_copy.exists() else pdf,
            images_dir=images_dir, n_images=len(list(images_dir.glob("*"))) if images_dir.exists() else 0,
            ocr_mode=ocr_mode, translated=True, skipped=True,
        )

    # ── 阶段 1：OCR ────────────────────────────────────────────
    cache_dir = out_root / ".cache" / stem
    extracted = cache_dir / "extracted"
    if use_cache and extracted.exists() and any(extracted.rglob("*.md")):
        say(f"[1/3] OCR：命中本地缓存，跳过云端解析（--refresh 可强制重跑）")
        say(f"        {extracted}")
    else:
        say(f"[1/3] OCR：MinerU 云 API（mode={ocr_mode}）解析 {pdf.name}")
        if use_cache and cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        extracted = ocr(pdf, cache_dir, mode=ocr_mode, model=ocr_model,
                       backend=backend, lang=lang, timeout=timeout, emit=say,
                       start_page=start_page, end_page=end_page)

    # ── 阶段 2：归位 ───────────────────────────────────────────
    say(f"[2/3] 归位：{out_dir}")
    org = organize(pdf, extracted, out_dir, keep_english=keep_english)
    say(f"        中文 MD 目标 {en_md.name}｜PDF 副本 {pdf_copy.name}｜"
        f"图片 {org.n_images} 张 → images/")

    result = ProcessResult(pdf=pdf, out_dir=out_dir, zh_md=zh_md, en_md=org.en_md,
                           pdf_copy=org.pdf_copy, images_dir=org.images_dir,
                           n_images=org.n_images, ocr_mode=ocr_mode,
                           translated=False)

    # ── 阶段 3：翻译 ────────────────────────────────────────────
    if skip_translate:
        say("[3/3] 翻译：已按参数跳过")
        result.notes.append("--skip-translate")
        return result

    cfg = resolve_translate(provider=provider, base_url=base_url, model=model,
                            api_key=api_key)
    say(f"[3/3] 翻译：{cfg.describe()}")
    try:
        translate_markdown(org.en_md, zh_md, cfg, chunk_size=chunk_size,
                          workers=workers, on_progress=on_progress, emit=say)
        result.translated = True
    except Exception as exc:
        if not keep_english:  # 英文原文都没留，就没得回滚了，只能抛
            raise
        result.notes.append(f"翻译失败：{exc}")
        say(f"  ✗ 翻译失败：{exc}")
        say(f"  ↳ 英文原文已保留：{org.en_md}")
    return result


def process_markdown(md: Path, out_root: Path, *,
                     provider: str | None = None, base_url: str | None = None,
                     model: str | None = None, api_key: str | None = None,
                     chunk_size: int = 3000, workers: int = 8,
                     force: bool = False, emit: Emit | None = None,
                     on_progress: Progress | None = None) -> ProcessResult:
    """已有英文 Markdown → 同样格式的交付目录（无 PDF 副本、无 OCR 图片）。

    产物：<out>/<md 名>/{<md 名>_en.md, <md 名>_zh.md}
    """
    say = emit or (lambda _line: None)
    md = Path(md)
    if not md.is_file():
        raise PipelineError(f"Markdown 不存在：{md}")
    out_root = Path(out_root)
    stem = safe_stem(md)
    out_dir = out_root / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    en_md = out_dir / f"{stem}_en.md"
    zh_md = out_dir / f"{stem}_zh.md"

    if zh_md.exists() and zh_md.stat().st_size > 0 and not force:
        say(f"⏭  已存在中文 MD，跳过：{zh_md}")
        return ProcessResult(pdf=md, out_dir=out_dir, zh_md=zh_md, en_md=en_md,
                             pdf_copy=md, images_dir=out_dir / "images",
                             n_images=0, ocr_mode="none", translated=True,
                             skipped=True)

    say(f"[1/2] 归位：{out_dir}")
    shutil.copy2(md, en_md)
    say(f"        英文原文 {en_md.name}")

    say("[2/2] 翻译")
    cfg = resolve_translate(provider=provider, base_url=base_url, model=model,
                            api_key=api_key)
    say(f"        {cfg.describe()}")
    result = ProcessResult(pdf=md, out_dir=out_dir, zh_md=zh_md, en_md=en_md,
                           pdf_copy=md, images_dir=out_dir / "images",
                           n_images=0, ocr_mode="none", translated=False)
    try:
        translate_markdown(en_md, zh_md, cfg, chunk_size=chunk_size,
                          workers=workers, on_progress=on_progress, emit=say)
        result.translated = True
    except Exception as exc:
        result.notes.append(f"翻译失败：{exc}")
        say(f"  ✗ 翻译失败：{exc}")
        say(f"  ↳ 英文原文已保留：{en_md}")
    return result
