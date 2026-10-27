"""Assemble the delivery directory: figures in, links rewritten, manifest written.

Invariants this module enforces (see ``references/output-contract.md``):

* every figure lands in ``images/`` and nothing else does;
* Markdown references are rewritten to ``images/<name>`` and never guessed;
* the source PDF is copied beside the Markdown so the directory is portable;
* runtime state (cache, checkpoints, temp files) stays outside the delivery tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import images as images_mod
import mdlinks
from errors import DeliveryError, ImageError

__all__ = [
    "Delivery",
    "Organized",
    "atomic_json",
    "atomic_text",
    "delivery_paths",
    "organize",
    "refuse_symlink",
    "safe_stem",
    "sha256_file",
]

_UNSAFE = set('/\\:*?"<>|')


def safe_stem(value: str | Path) -> str:
    """One portable directory name for a PDF: no separators, <=120 code points."""
    raw = Path(value).stem.strip() or "document"
    cleaned = "".join("_" if char in _UNSAFE or ord(char) < 32 else char for char in raw)
    collapsed = " ".join(cleaned.split())
    return collapsed.strip(" .")[:120] or "document"


@dataclass(frozen=True)
class Delivery:
    """Every path one PDF owns. Computed once per run."""

    out_root: Path
    stem: str
    out_dir: Path
    markdown: Path
    chinese: Path
    pdf_copy: Path
    images_dir: Path
    meta_dir: Path
    sidecar: Path
    manifest: Path
    validation: Path
    completion: Path
    cache_root: Path
    checkpoint_dir: Path


def delivery_paths(out_root: str | Path, stem: str) -> Delivery:
    root = Path(out_root).expanduser().absolute()
    out_dir = root / stem
    return Delivery(
        out_root=root,
        stem=stem,
        out_dir=out_dir,
        markdown=out_dir / f"{stem}.md",
        chinese=out_dir / f"{stem}_zh.md",
        pdf_copy=out_dir / f"{stem}.pdf",
        images_dir=out_dir / "images",
        meta_dir=out_dir / "_meta",
        sidecar=out_dir / "_meta" / "content_list.json",
        manifest=out_dir / "_meta" / "manifest.json",
        validation=out_dir / "_meta" / "validation.json",
        completion=out_dir / "_meta" / "completion.json",
        cache_root=root / ".cache",
        checkpoint_dir=root / ".checkpoints",
    )


def refuse_symlink(path: str | Path, root: str | Path) -> Path:
    """Reject a writable path when any component under ``root`` is a symlink."""
    target = Path(os.path.abspath(os.fspath(path)))
    anchor = Path(os.path.abspath(os.fspath(root)))
    try:
        target.relative_to(anchor)
    except ValueError as exc:
        raise DeliveryError(f"路径越界：{target}（根目录 {anchor}）") from exc
    current = target
    while True:
        if current.is_symlink():
            raise DeliveryError(f"拒绝通过软链接写入：{current}")
        if current == anchor:
            return target
        if current.parent == current:
            raise DeliveryError(f"输出根目录不合法：{anchor}")
        current = current.parent


def atomic_text(path: str | Path, text: str, *, root: str | Path | None = None) -> Path:
    """Write via a sibling temp file + one ``os.replace``."""
    destination = Path(path)
    if root is not None:
        refuse_symlink(destination, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def atomic_json(path: str | Path, payload: object, *, root: str | Path | None = None) -> Path:
    return atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       root=root)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Organized:
    delivery: Delivery
    markdown: Path
    images: tuple[dict, ...]
    pdf_copy: Path


def _source_markdown(extracted: Path, preferred: Path | None) -> Path:
    if preferred is not None:
        return preferred
    for name in ("full.md", "full.txt"):
        candidate = extracted / name
        if candidate.is_file():
            return candidate
    matches = sorted((path for path in extracted.rglob("*.md") if path.is_file()),
                     key=lambda path: (len(path.parts), str(path)))
    if not matches:
        raise DeliveryError(f"解压结果中没有 Markdown：{extracted}")
    return matches[0]


def _collect_images(extracted: Path) -> list[Path]:
    found = [path for path in extracted.rglob("*") if images_mod.is_image_file(path)]
    return sorted(found, key=lambda path: ("images" not in path.parts[:-1], str(path)))


def organize(pdf: str | Path, extracted: str | Path, delivery: Delivery, *,
             channel: str, params: dict, markdown: Path | None = None,
             content_list: Path | None = None) -> Organized:
    """Copy figures, rewrite references, write Markdown and the audit manifest."""
    source_pdf, extracted_root = Path(pdf), Path(extracted)
    for path in (delivery.out_dir, delivery.images_dir, delivery.markdown,
                 delivery.pdf_copy, delivery.meta_dir):
        refuse_symlink(path, delivery.out_root)
    delivery.out_dir.mkdir(parents=True, exist_ok=True)
    delivery.images_dir.mkdir(parents=True, exist_ok=True)
    delivery.meta_dir.mkdir(parents=True, exist_ok=True)

    source_digest = sha256_file(source_pdf)
    if not (delivery.pdf_copy.is_file() and sha256_file(delivery.pdf_copy) == source_digest):
        refuse_symlink(delivery.pdf_copy, delivery.out_dir)
        delivery.pdf_copy.write_bytes(source_pdf.read_bytes())

    source_markdown = _source_markdown(extracted_root, markdown)
    text = source_markdown.read_text(encoding="utf-8")
    pixel_sources = _collect_images(extracted_root)

    mapping: dict[str, str] = {}
    entries: list[dict] = []
    names: dict[str, list[Path]] = {}
    for path in pixel_sources:
        names.setdefault(path.name, []).append(path)
    for path in pixel_sources:
        try:
            destination, entry = images_mod.normalize(path, delivery.images_dir)
        except ImageError as exc:
            raise DeliveryError(str(exc)) from exc
        entry["source"] = path.relative_to(extracted_root).as_posix()
        entries.append(entry)
        relative = destination.relative_to(delivery.out_dir).as_posix()
        keys = [str(path), path.relative_to(extracted_root).as_posix(),
                os.path.relpath(path, source_markdown.parent).replace(os.sep, "/")]
        if len(names[path.name]) == 1:
            keys.append(path.name)
        for key in keys:
            mapping[mdlinks.canonical_ref(key)] = relative

    rewritten = mdlinks.rewrite(text, mapping)
    atomic_text(delivery.markdown, rewritten, root=delivery.out_dir)

    if content_list is not None and content_list.is_file():
        refuse_symlink(delivery.sidecar, delivery.out_dir)
        delivery.sidecar.write_bytes(content_list.read_bytes())

    atomic_json(delivery.manifest, {
        "version": 1,
        "stem": delivery.stem,
        "channel": channel,
        "params": params,
        "source": {
            "name": source_pdf.name,
            "sha256": source_digest,
            "bytes": source_pdf.stat().st_size,
        },
        "markdown": delivery.markdown.name,
        "images": entries,
    }, root=delivery.out_dir)
    return Organized(delivery=delivery, markdown=delivery.markdown,
                     images=tuple(entries), pdf_copy=delivery.pdf_copy)
