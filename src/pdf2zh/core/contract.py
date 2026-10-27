"""Provider-independent image normalization, link rewriting, and delivery checks."""
from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import warnings
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
_FORMATS = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"),
            "GIF": ("image/gif", ".gif"), "WEBP": ("image/webp", ".webp"),
            "BMP": ("image/bmp", ".bmp"), "TIFF": ("image/tiff", ".tiff")}
_ALIASES = {"image/jpeg": {".jpg", ".jpeg"}, "image/tiff": {".tif", ".tiff"}}
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)")
_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_INDENTED_CODE = re.compile(r"(?m)^(?: {4}|\t).*(?:\n|$)")
_HTML_SRC = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
_DEFINITION = re.compile(r"(?m)^ {0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]*)>|([^\s]+))")


@dataclass(frozen=True)
class ImageLink:
    start: int
    end: int
    source: str


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def image_link_spans(text: str) -> list[ImageLink]:
    """Locate image destinations, including HTML and reference-style Markdown.

    Source spans let rewriting leave captions, titles, whitespace and code alone.
    """
    protected = [(m.start(), m.end()) for regex in (_COMMENT, _INDENTED_CODE, _INLINE_CODE)
                 for m in regex.finditer(text)]
    lines = text.splitlines(keepends=True)
    offset, fence = 0, None
    for line in lines:
        match = _FENCE_OPEN.match(line)
        if fence is None and match:
            fence = (match.group(1)[0], len(match.group(1)))
            start = offset
        elif (fence is not None and match and match.group(1)[0] == fence[0]
              and len(match.group(1)) >= fence[1]
              and not match.group(2).strip()):
            protected.append((start, offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        protected.append((start, len(text)))

    def in_code(pos: int) -> bool:
        return any(start <= pos < end for start, end in protected)

    links: list[ImageLink] = []
    for match in _HTML_SRC.finditer(text):
        if not in_code(match.start()):
            group = next(i for i in (1, 2, 3) if match.group(i) is not None)
            links.append(ImageLink(match.start(group), match.end(group), html.unescape(match.group(group))))
    image_labels: set[str] = set()
    for match in re.finditer(r"(?<!\\)!\[", text):
        if in_code(match.start()):
            continue
        i, depth = match.end(), 1
        while i < len(text) and depth:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
            i += 1
        if depth:
            continue
        alt = text[match.end():i - 1]
        if i < len(text) and text[i] == "(":
            i += 1
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "<":
                start = i + 1
                end = text.find(">", start)
                if end >= 0:
                    links.append(ImageLink(start, end, text[start:end]))
                continue
            start, depth = i, 0
            while i < len(text):
                char = text[i]
                if char == "\\":
                    i += 2
                    continue
                if char == "(":
                    depth += 1
                elif char == ")":
                    if depth == 0:
                        break
                    depth -= 1
                elif char.isspace() and depth == 0:
                    break
                i += 1
            if i > start:
                source = re.sub(r"\\([() ])", r"\1", text[start:i])
                links.append(ImageLink(start, i, source))
        elif i < len(text) and text[i] == "[":
            end = text.find("]", i + 1)
            if end >= 0:
                image_labels.add(_label(text[i + 1:end] or alt))
        else:
            image_labels.add(_label(alt))
    for match in _DEFINITION.finditer(text):
        if _label(match.group(1)) in image_labels and not in_code(match.start()):
            group = 2 if match.group(2) is not None else 3
            links.append(ImageLink(match.start(group), match.end(group), match.group(group)))
    return sorted({(v.start, v.end): v for v in links}.values(), key=lambda v: v.start)


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            self.refs.extend(value for key, value in attrs if key.lower() == "src" and value is not None)


def extract_image_refs(text: str) -> list[str]:
    """Read the actual rendered image sequence, ignoring examples in code."""
    refs: list[str] = []

    def visit(tokens):
        for token in tokens:
            if token.type == "image":
                refs.append(token.attrGet("src") or "")
            elif token.type in {"html_inline", "html_block"}:
                parser = _ImageParser()
                parser.feed(token.content)
                refs.extend(parser.refs)
            elif token.children:
                visit(token.children)
    visit(MarkdownIt("default", {"html": True}).parse(text))
    return refs


def canonical_ref(value: str) -> str:
    return os.path.normpath(unquote(html.unescape(value)).replace("\\", "/")).replace("\\", "/")


def rewrite_image_links(text: str, mapping: dict[str, str]) -> str:
    """Rewrite only exact, normalized destinations; never guess ambiguous basenames."""
    normalized = {canonical_ref(key): value for key, value in mapping.items()}
    for link in reversed(image_link_spans(text)):
        replacement = mapping.get(link.source) or normalized.get(canonical_ref(link.source))
        if replacement is not None:
            text = text[:link.start] + replacement + text[link.end:]
    return text


def _image_metadata(path: Path) -> tuple[str, str, int | None, int | None]:
    try:
        with path.open("rb") as stream:
            head = stream.read(4096).lstrip()
        if path.suffix.lower() == ".svg" or b"<svg" in head[:512]:
            raw = path.read_bytes()
            lowered = raw.lower()
            if (b"<!doctype" in lowered or b"<!entity" in lowered or b"@import" in lowered
                    or b"url(" in lowered or re.search(rb"<(script|foreignobject|iframe|animate\w*|set|style)\b", lowered)):
                raise ValueError("SVG 含 DTD、外部样式或活动内容")
            root = ET.fromstring(raw)
            if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                raise ValueError("SVG 根节点无效")
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1].lower()
                if tag in {"script", "foreignobject", "iframe"}:
                    raise ValueError("SVG 含活动内容")
                for attr, value in element.attrib.items():
                    attr = attr.rsplit("}", 1)[-1].lower()
                    if attr.startswith("on") or (attr == "href" and not value.startswith("#")):
                        raise ValueError("SVG 含活动属性/外部资源")
            return "image/svg+xml", ".svg", None, None
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                fmt = image.format
                image.verify()
            with Image.open(path) as image:
                image.load()  # verify alone misses truncated pixel data in some formats.
                width, height = image.size
        if fmt not in _FORMATS:
            raise ValueError(f"暂不支持图片编码 {fmt}")
        mime, extension = _FORMATS[fmt]
        return mime, extension, width, height
    except (OSError, ValueError, ET.ParseError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"无法解码图片 {path.name}：{exc}") from exc


def is_image_file(path: Path) -> bool:
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return False
    if path.suffix.lower() in IMAGE_EXTS:
        return True
    with path.open("rb") as stream:
        head = stream.read(512).lstrip()
    return head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")) or (head.startswith(b"RIFF") and head[8:12] == b"WEBP") or b"<svg" in head


def normalize_image(src: Path, dest_dir: Path, preferred_name: str | None = None) -> tuple[Path, dict]:
    """Copy losslessly, fix mismatched extensions, reuse identical assets safely."""
    src, dest_dir = Path(src), Path(dest_dir)
    mime, extension, width, height = _image_metadata(src)
    raw = src.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(preferred_name or src.name).stem).strip("._") or "image"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{extension}"
    if dest.exists() and dest.read_bytes() != raw:
        dest = dest_dir / f"{stem}-{digest[:16]}{extension}"
    if dest.is_symlink() or (dest.exists() and dest.read_bytes() != raw):
        raise ValueError(f"图片目标冲突：{dest.name}")
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest, {"source": src.name, "output": dest.relative_to(dest_dir.parent).as_posix(),
                  "mime": mime, "width": width, "height": height, "bytes": len(raw),
                  "sha256": digest, "fallback": False}


def validate_delivery(en_md: Path, zh_md: Path | None, images_dir: Path) -> dict:
    """Hard checks: exact paths, decoding, encoding, bilingual image sequence."""
    images_dir = Path(images_dir)
    issues: list[dict] = []
    soft: list[dict] = []
    references: dict[str, list[str]] = {}
    inspected: set[Path] = set()
    for language, md in (("en", Path(en_md)), ("zh", Path(zh_md) if zh_md is not None else None)):
        if md is None:
            continue
        if not md.is_file() or not md.stat().st_size:
            issues.append({"code": "missing_markdown", "language": language})
            continue
        text = md.read_text(encoding="utf-8", errors="strict")
        refs = extract_image_refs(text)
        references[language] = refs
        for ref in refs:
            url = urlsplit(ref)
            if url.scheme in {"http", "https", "data"}:
                issues.append({"code": "external_image", "language": language, "ref": ref[:200]})
                continue
            value = unquote(url.path).replace("\\", "/")
            if url.scheme or url.netloc or not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value) or ".." in Path(value).parts or ".cache" in Path(value).parts:
                issues.append({"code": "non_portable_image_path", "language": language, "ref": ref})
                continue
            candidate = (md.parent / value).resolve()
            if not candidate.is_relative_to(images_dir.resolve()) or (md.parent / value).is_symlink():
                issues.append({"code": "image_outside_assets", "language": language, "ref": ref})
            elif not candidate.is_file():
                issues.append({"code": "missing_image", "language": language, "ref": ref})
            else:
                inspected.add(candidate)
    if "zh" in references and "en" in references:
        en_seq = [canonical_ref(value) for value in references["en"]]
        zh_seq = [canonical_ref(value) for value in references["zh"]]
        if en_seq != zh_seq:
            issues.append({"code": "image_reference_parity_mismatch",
                           "en_count": len(en_seq), "zh_count": len(zh_seq),
                           "en_sequence": en_seq, "zh_sequence": zh_seq})
    assets = [p for p in images_dir.rglob("*") if p.is_file()] if images_dir.is_dir() else []
    for path in sorted(set(assets) | inspected):
        if path.is_symlink() or not path.resolve().is_relative_to(images_dir.resolve()):
            issues.append({"code": "image_outside_assets", "ref": path.name})
            continue
        try:
            mime, extension, width, height = _image_metadata(path)
            if path.suffix.lower() not in _ALIASES.get(mime, {extension}):
                issues.append({"code": "extension_mismatch", "ref": path.name, "expected": extension})
            if width is not None and height is not None and (width < 16 or height < 16):
                soft.append({"code": "tiny_image", "ref": path.name, "width": width, "height": height})
        except ValueError as exc:
            issues.append({"code": "undecodable_image", "ref": path.name, "detail": str(exc)})
    return {"ok": not issues, "issues": issues, "warnings": soft, "references": references, "image_count": len(assets)}


def _reject_symlink_tree(path: Path, root: Path) -> None:
    path, root = Path(path), Path(root)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"路径越界：{path}") from exc
    current = path
    while True:
        if current.is_symlink():
            raise ValueError(f"交付路径不能包含软链接：{current}")
        if current == root:
            break
        if current.parent == current:
            raise ValueError(f"路径根非法：{root}")
        current = current.parent


def create_delivery_zip(out_dir: Path, destination: Path, *, include_chinese: bool = True) -> Path:
    """Package the portable document, excluding partial/temporary files."""
    out_dir, destination = Path(out_dir), Path(destination)
    if destination.resolve().is_relative_to(out_dir.resolve()):
        raise ValueError("ZIP 必须位于交付子目录外")
    destination.parent.mkdir(parents=True, exist_ok=True)
    allowed_files = {".pdf", ".md", ".html"}
    allowed_names = {"manifest.json", "validation.json"}
    allowed_sidecars = {"layout.json", "content_list.json", "middle.json", "model.json"}
    _reject_symlink_tree(out_dir, out_dir.parent)
    _reject_symlink_tree(destination.parent, destination.parent.parent)
    if destination.exists() and destination.is_symlink():
        raise ValueError(f"ZIP 目标不能是软链接：{destination}")
    stem = out_dir.name
    allowed_root_names = {
        f"{stem}.pdf", f"{stem}_en.md", f"{stem}_zh.md",
        f"{stem}_en.html", f"{stem}_zh.html", *allowed_names,
    }
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                continue
            relative = path.relative_to(out_dir)
            top = relative.parts[0] if relative.parts else ""
            if top == "images":
                if not is_image_file(path):
                    continue
            elif path.name in allowed_names:
                pass
            elif top == "_mineru" and path.name in allowed_sidecars:
                pass
            elif len(relative.parts) == 1 and path.name in allowed_root_names:
                pass
            else:
                continue
            if not include_chinese and path.name.endswith("_zh.md"):
                continue
            archive.write(path, (Path(out_dir.name) / relative).as_posix())
    return destination
