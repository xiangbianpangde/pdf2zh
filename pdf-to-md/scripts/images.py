"""Image inspection and normalization.

The extension of an extracted file is not evidence: MinerU (and anything else)
can emit PNG bytes named ``figure.jpg``. Format is decided from magic bytes and,
when Pillow happens to be importable, confirmed by a real decode. Pillow is
optional on purpose — the skill must run on a bare Python 3.10+ install.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from errors import ImageError

__all__ = ["ImageError", "ImageInfo", "is_image_file", "inspect", "normalize"]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
_MIME_BY_FORMAT = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
    "BMP": ("image/bmp", ".bmp"),
    "TIFF": ("image/tiff", ".tiff"),
}
MIME_SUFFIXES = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "image/bmp": {".bmp"},
    "image/tiff": {".tif", ".tiff"},
    "image/svg+xml": {".svg"},
}


@dataclass(frozen=True)
class ImageInfo:
    mime: str
    suffix: str
    width: int | None
    height: int | None
    size: int
    sha256: str
    verifier: str

    def manifest(self) -> dict:
        return {
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "bytes": self.size,
            "sha256": self.sha256,
            "verified_by": self.verifier,
        }


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if head.startswith(b"BM"):
        return "BMP"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def _dimensions(fmt: str, raw: bytes) -> tuple[int | None, int | None]:
    try:
        if fmt == "PNG":
            return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if fmt == "GIF":
            return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
        if fmt == "BMP":
            return int.from_bytes(raw[18:22], "little", signed=True), abs(
                int.from_bytes(raw[22:26], "little", signed=True))
        if fmt == "JPEG":
            index = 2
            while index + 9 < len(raw):
                if raw[index] != 0xFF:
                    index += 1
                    continue
                marker = raw[index + 1]
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height = int.from_bytes(raw[index + 5:index + 7], "big")
                    width = int.from_bytes(raw[index + 7:index + 9], "big")
                    return width, height
                if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                index += 2 + int.from_bytes(raw[index + 2:index + 4], "big")
            return None, None
        if fmt == "WEBP":
            chunk = raw[12:16]
            if chunk == b"VP8X":
                width = int.from_bytes(raw[24:27], "little") + 1
                height = int.from_bytes(raw[27:30], "little") + 1
                return width, height
            if chunk == b"VP8 ":
                width = int.from_bytes(raw[26:28], "little") & 0x3FFF
                height = int.from_bytes(raw[28:30], "little") & 0x3FFF
                return width, height
            if chunk == b"VP8L":
                bits = int.from_bytes(raw[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return None, None
    except (IndexError, ValueError):
        return None, None
    return None, None


def _verify_svg(raw: bytes) -> None:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered or b"@import" in lowered or b"url(" in lowered:
        raise ImageError("SVG 含 DTD/外部样式/外部引用")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ImageError(f"SVG 无法解析：{exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ImageError("SVG 根节点无效")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject", "iframe", "animate", "set", "style"}:
            raise ImageError(f"SVG 含活动内容 <{tag}>")
        for name, value in element.attrib.items():
            attribute = name.rsplit("}", 1)[-1].lower()
            if attribute.startswith("on"):
                raise ImageError("SVG 含事件属性")
            if attribute == "href" and not value.startswith("#"):
                raise ImageError("SVG 含外部资源引用")


def _verify_with_pillow(path: Path) -> bool:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
        return True
    except Exception as exc:  # noqa: BLE001 - any decoder failure disqualifies the asset
        raise ImageError(f"无法解码图片 {path.name}：{exc}") from exc


def inspect(path: str | Path) -> ImageInfo:
    """Decide format, suffix and dimensions from content, not from the file name."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ImageError(f"无法读取 {target.name}：{exc}") from exc
    if not raw:
        raise ImageError(f"空文件：{target.name}")
    digest = hashlib.sha256(raw).hexdigest()
    head = raw[:4096].lstrip()
    if target.suffix.lower() == ".svg" or b"<svg" in head[:512].lower():
        _verify_svg(raw)
        return ImageInfo("image/svg+xml", ".svg", None, None, len(raw), digest, "stdlib")

    fmt = _sniff(raw)
    if fmt is None:
        raise ImageError(f"无法识别的图片编码：{target.name}")
    mime, suffix = _MIME_BY_FORMAT[fmt]
    width, height = _dimensions(fmt, raw)
    verifier = "pillow" if _verify_with_pillow(target) else "header"
    return ImageInfo(mime, suffix, width, height, len(raw), digest, verifier)


def is_image_file(path: str | Path) -> bool:
    """Cheap gate used when walking an extraction directory."""
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        return False
    try:
        head = target.open("rb").read(512)
    except OSError:
        return False
    if _sniff(head) is not None:
        return True
    if target.suffix.lower() in IMAGE_SUFFIXES:
        return True
    return b"<svg" in head.lstrip()[:512].lower()


def _safe_stem(name: str, digest: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._")
    return stem or f"image-{digest[:8]}"


def normalize(source: str | Path, destination_dir: str | Path,
              preferred_name: str | None = None) -> tuple[Path, dict]:
    """Copy one image into ``destination_dir`` with a content-correct suffix.

    Identical bytes reuse the same file; different bytes under the same name get a
    digest suffix instead of silently overwriting.
    """
    src, dest_dir = Path(source), Path(destination_dir)
    info = inspect(src)
    raw = src.read_bytes()
    stem = _safe_stem(preferred_name or src.name, info.sha256)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{stem}{info.suffix}"
    if target.exists():
        if target.is_symlink():
            raise ImageError(f"图片目标不能是软链接：{target.name}")
        if target.read_bytes() != raw:
            target = dest_dir / f"{stem}-{info.sha256[:12]}{info.suffix}"
    if target.exists() and target.read_bytes() != raw:
        raise ImageError(f"图片目标冲突：{target.name}")
    if not target.exists():
        shutil.copy2(src, target)
    entry = {"source": src.name, "output": f"images/{target.name}", **info.manifest()}
    return target, entry
