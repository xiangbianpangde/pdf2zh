"""批量把 USB 说明文档目录里的 PDF 转成 Markdown（遗留：本机 mineru CLI）。

新项目默认走云 OCR 一站式管线（英文 PDF → 中文 md + pdf 副本 + images/）：
    pdf2zh-pipeline <pdf>... -o <out>
本脚本保留给「只要英文 MD、不翻译、跑本机模型」的批量场景。

设计：
- 直接调 mineru CLI（pipeline 后端），不走 mineru-api 避免每次起子进程加载模型
- 输出按 USB 相对路径镜像到 OUT_ROOT，保留中英子目录结构
- 跳过已存在的 .md（断点续跑）
- 单文件失败不中断，错误码记入日志
- 中文 PDF 不翻译（本身已是中文），英文 Manual 也不翻译（用户只要 MD）

用法：
    source .venv/bin/activate
    python tools/batch_pdf_to_md.py

输出目录默认 ~/Documents/usb_md，可用 PDF2ZH_USB_OUT 覆盖；
U 盘源目录可用 PDF2ZH_USB_ROOT 覆盖。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

USB_ROOT = Path(os.getenv("PDF2ZH_USB_ROOT", "/Volumes/xbpd的u盘/说明文档目录"))
DEFAULT_OUT = Path(os.getenv("PDF2ZH_USB_OUT", str(Path.home() / "Documents" / "usb_md")))
DEFAULT_BACKEND = "pipeline"
DEFAULT_LANG = "ch"  # 中文为主；英文 PDF 也能识别

# mineru 装在项目 .venv/bin/ 下但未必在 PATH，
# 让 subprocess 能直接调 mineru / mineru-api 等命令。
PROJECT_VENV_BIN = Path(__file__).resolve().parent.parent / ".venv" / "bin"
os.environ["PATH"] = (
    str(PROJECT_VENV_BIN) + os.pathsep + os.environ.get("PATH", "")
)


def find_pdfs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def already_done(pdf: Path, out_root: Path) -> Path | None:
    rel = pdf.relative_to(USB_ROOT)
    out_dir = out_root / rel.parent
    md = out_dir / (pdf.stem + ".md")
    return md if md.exists() and md.stat().st_size > 0 else None


def convert_one(pdf: Path, out_root: Path, backend: str, lang: str,
                timeout: int) -> tuple[bool, str, float]:
    rel = pdf.relative_to(USB_ROOT)
    out_dir = out_root / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    md_target = out_dir / (pdf.stem + ".md")

    cmd = [
        "mineru",
        "-p", str(pdf),
        "-o", str(out_dir),
        "-b", backend,
        "-l", lang,
        "-m", "auto",
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s", time.time() - t0
    elapsed = time.time() - t0
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        return False, f"exit={proc.returncode} tail={tail}", elapsed

    candidates = list(out_dir.rglob(f"{pdf.stem}.md"))
    candidates = [c for c in candidates if c != md_target]
    if not candidates:
        return False, "no .md produced", elapsed
    src = candidates[0]
    if md_target.exists():
        md_target.unlink()
    shutil.move(str(src), str(md_target))
    leftover_dir = src.parent.parent if src.parent.name == "auto" else None
    if leftover_dir and leftover_dir.exists() and leftover_dir != out_dir:
        shutil.rmtree(leftover_dir, ignore_errors=True)
    return True, f"-> {md_target} ({md_target.stat().st_size} bytes)", elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"输出根目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--src", type=Path, default=USB_ROOT,
                    help=f"PDF 源根（默认 {USB_ROOT}）")
    ap.add_argument("--backend", default=DEFAULT_BACKEND,
                    help=f"MinerU 后端（默认 {DEFAULT_BACKEND}）")
    ap.add_argument("--lang", default=DEFAULT_LANG,
                    help=f"OCR 语言提示（默认 {DEFAULT_LANG}）")
    ap.add_argument("--timeout", type=int, default=900,
                    help="单文件超时秒数（默认 900）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 个（0=全部），用于先验")
    ap.add_argument("--filter", default="",
                    help="只跑路径含此子串的 PDF（可选）")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"[FATAL] source not found: {args.src}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)

    pdfs = find_pdfs(args.src)
    if args.filter:
        pdfs = [p for p in pdfs if args.filter in str(p)]
    if args.limit:
        pdfs = pdfs[: args.limit]

    print(f"源: {args.src}")
    print(f"出: {args.out}")
    print(f"后端: {args.backend}  OCR lang: {args.lang}  timeout: {args.timeout}s")
    print(f"PDF 总数: {len(pdfs)}")
    print()

    started_at = time.time()
    done = skipped = failed = 0
    log_path = args.out / "_batch.log"
    log_fh = open(log_path, "a", encoding="utf-8")

    for idx, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(args.src)
        if (md := already_done(pdf, args.out)):
            skipped += 1
            print(f"[{idx}/{len(pdfs)}] SKIP {rel.name} (→ {md.name})")
            continue

        print(f"[{idx}/{len(pdfs)}] RUN  {rel}", flush=True)
        ok, info, elapsed = convert_one(
            pdf, args.out, args.backend, args.lang, args.timeout,
        )
        status = "OK  " if ok else "FAIL"
        line = (f"{time.strftime('%H:%M:%S')} {status} [{idx}/{len(pdfs)}] "
                f"{elapsed:6.1f}s  {rel}  | {info}\n")
        log_fh.write(line)
        log_fh.flush()
        if ok:
            done += 1
        else:
            failed += 1

    total_elapsed = time.time() - started_at
    summary = (
        f"\n=== 汇总 ===\n"
        f"  总数:   {len(pdfs)}\n"
        f"  成功:   {done}\n"
        f"  跳过:   {skipped}\n"
        f"  失败:   {failed}\n"
        f"  耗时:   {total_elapsed/60:.1f} min\n"
        f"  日志:   {log_path}\n"
    )
    print(summary)
    log_fh.write(summary)
    log_fh.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())