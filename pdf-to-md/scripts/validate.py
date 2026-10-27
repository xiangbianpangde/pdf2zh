"""Hard checks on a delivery directory.

A delivery is only valid when every referenced figure exists, decodes, carries a
content-correct suffix, lives inside ``images/``, and the Chinese version
references the same figures in the same order as the English one. Failures are
reported as issue codes, never as a warning plus a "done" message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import images as images_mod
import mdlinks
from errors import DeliveryError, ImageError

__all__ = ["Report", "validate"]

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class Report:
    ok: bool
    issues: tuple[dict, ...]
    warnings: tuple[dict, ...]
    image_count: int
    references: dict[str, list[str]]

    def codes(self) -> list[str]:
        return sorted({str(issue.get("code")) for issue in self.issues})

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "image_count": self.image_count,
            "references": self.references,
        }

    def summary(self) -> str:
        if self.ok:
            extra = f"，{len(self.warnings)} 条警告" if self.warnings else ""
            return f"校验通过（{self.image_count} 张图片{extra}）"
        return "校验失败：" + "、".join(self.codes())


def _portable(value: str) -> tuple[bool, str]:
    """Return (is_portable, normalized_posix_path)."""
    parsed = urlsplit(value)
    path = unquote(parsed.path).replace("\\", "/")
    if parsed.scheme or parsed.netloc:
        return False, path
    if not path or path.startswith("/") or _WINDOWS_ABSOLUTE.match(path):
        return False, path
    if ".." in Path(path).parts or ".cache" in Path(path).parts:
        return False, path
    return True, path


def validate(markdown: str | Path, chinese: str | Path | None,
             images_dir: str | Path) -> Report:
    """Run every hard rule. The first failure does not stop later checks."""
    english = Path(markdown)
    assets_root = Path(images_dir)
    issues: list[dict] = []
    warnings: list[dict] = []
    references: dict[str, list[str]] = {}
    inspected: set[Path] = set()
    assets_resolved = None
    try:
        assets_resolved = assets_root.resolve(strict=False)
    except OSError:
        assets_resolved = assets_root.absolute()

    for language, document in (("en", english), ("zh", Path(chinese) if chinese else None)):
        if document is None:
            continue
        if not document.is_file() or document.stat().st_size == 0:
            issues.append({"code": "missing_markdown", "language": language,
                           "path": document.name})
            continue
        try:
            text = document.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            issues.append({"code": "unreadable_markdown", "language": language,
                           "detail": str(exc)})
            continue
        refs = list(mdlinks.image_refs(text))
        references[language] = refs
        for ref in refs:
            portable, value = _portable(ref)
            if not portable:
                code = "external_image" if urlsplit(ref).scheme else "non_portable_image_path"
                issues.append({"code": code, "language": language, "ref": ref[:200]})
                continue
            candidate = document.parent / value
            try:
                resolved = candidate.resolve(strict=False)
            except OSError:
                resolved = candidate.absolute()
            if candidate.is_symlink() or resolved != assets_resolved and assets_resolved not in resolved.parents:
                issues.append({"code": "image_outside_assets", "language": language, "ref": ref})
            elif not candidate.is_file():
                issues.append({"code": "missing_image", "language": language, "ref": ref})
            else:
                inspected.add(candidate)

    if "en" in references and "zh" in references:
        en_sequence = [mdlinks.canonical_ref(ref) for ref in references["en"]]
        zh_sequence = [mdlinks.canonical_ref(ref) for ref in references["zh"]]
        if en_sequence != zh_sequence:
            issues.append({
                "code": "image_reference_parity_mismatch",
                "en_count": len(en_sequence),
                "zh_count": len(zh_sequence),
                "en_sequence": en_sequence,
                "zh_sequence": zh_sequence,
            })

    assets = [path for path in assets_root.rglob("*") if path.is_file()] if assets_root.is_dir() else []
    for path in sorted(set(assets) | inspected):
        if path.is_symlink():
            issues.append({"code": "image_outside_assets", "ref": path.name})
            continue
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
        if resolved != assets_resolved and assets_resolved not in resolved.parents:
            issues.append({"code": "image_outside_assets", "ref": path.name})
            continue
        try:
            info = images_mod.inspect(path)
        except (ImageError, ValueError, OSError) as exc:
            issues.append({"code": "undecodable_image", "ref": path.name, "detail": str(exc)})
            continue
        allowed = images_mod.MIME_SUFFIXES.get(info.mime, {info.suffix})
        if path.suffix.lower() not in allowed:
            issues.append({"code": "extension_mismatch", "ref": path.name,
                           "mime": info.mime, "expected": info.suffix})
        if info.width is not None and info.height is not None and (info.width < 16 or info.height < 16):
            warnings.append({"code": "tiny_image", "ref": path.name,
                             "width": info.width, "height": info.height})

    return Report(ok=not issues, issues=tuple(issues), warnings=tuple(warnings),
                  image_count=len(assets), references=references)
