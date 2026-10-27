"""Markdown image references: locate destinations, rewrite them, read them in order.

The parser is deliberately hand-written and dependency-free. It must:

* rewrite only the destination part, leaving alt text, titles and code untouched;
* never touch examples that live inside fenced blocks, inline code or comments;
* report referenced images in **usage order** (reference-style definitions may sit
  anywhere in the file, so a plain text scan would mis-order them);
* never guess a basename that is ambiguous — ``rewrite`` applies exact matches only.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from urllib.parse import unquote

__all__ = ["Link", "Scan", "canonical_ref", "image_refs", "rewrite", "scan"]

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)")
_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_INDENTED_CODE = re.compile(r"(?m)^(?: {4}|\t).*(?:\n|$)")
_HTML_SRC = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
_DEFINITION = re.compile(r"(?m)^ {0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]*)>|([^\s]+))")


@dataclass(frozen=True)
class Link:
    """A rewritable destination span inside the source text."""

    start: int
    end: int
    source: str


@dataclass(frozen=True)
class Scan:
    """One pass over a document: spans to rewrite, plus destinations in usage order."""

    links: tuple[Link, ...]
    refs: tuple[str, ...]


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def canonical_ref(value: str) -> str:
    """Normalized comparison form of a reference (unquoted, slashes unified)."""
    return os.path.normpath(unquote(html.unescape(value)).replace("\\", "/")).replace("\\", "/")


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    """Ranges where image syntax is literal, not a live reference."""
    ranges: list[tuple[int, int]] = [
        (match.start(), match.end())
        for regex in (_COMMENT, _INDENTED_CODE, _INLINE_CODE)
        for match in regex.finditer(text)
    ]
    offset = 0
    fence: tuple[str, int] | None = None
    fence_start = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE_OPEN.match(line)
        if fence is None and match:
            fence = (match.group(1)[0], len(match.group(1)))
            fence_start = offset
        elif (fence is not None and match and match.group(1)[0] == fence[0]
              and len(match.group(1)) >= fence[1]
              and not match.group(2).strip()):
            ranges.append((fence_start, offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        ranges.append((fence_start, len(text)))
    return ranges


def _inline_destination(text: str, index: int) -> tuple[int, int, str] | None:
    """Parse ``(dest "title")`` starting just after ``(``. Returns the dest span."""
    length = len(text)
    while index < length and text[index].isspace():
        index += 1
    if index >= length:
        return None
    if text[index] == "<":
        start = index + 1
        end = text.find(">", start)
        if end < 0:
            return None
        return start, end, text[start:end]
    start = index
    depth = 0
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        elif char.isspace() and depth == 0:
            break
        index += 1
    if index <= start:
        return None
    source = re.sub(r"\\([()<> ])", r"\1", text[start:index])
    return start, index, source


def scan(text: str) -> Scan:
    """Single pass: spans to rewrite and image destinations in document order."""
    protected = _protected_ranges(text)

    def in_code(position: int) -> bool:
        return any(start <= position < end for start, end in protected)

    definitions: dict[str, Link] = {}
    for match in _DEFINITION.finditer(text):
        if in_code(match.start()):
            continue
        group = 2 if match.group(2) is not None else 3
        definitions.setdefault(_label(match.group(1)),
                               Link(match.start(group), match.end(group), match.group(group)))

    occurrences: list[tuple[int, str]] = []
    spans: list[Link] = []
    used_labels: set[str] = set()

    for match in _HTML_SRC.finditer(text):
        if in_code(match.start()):
            continue
        group = next(index for index in (1, 2, 3) if match.group(index) is not None)
        source = html.unescape(match.group(group))
        spans.append(Link(match.start(group), match.end(group), source))
        occurrences.append((match.start(), source))

    for match in re.finditer(r"(?<!\\)!\[", text):
        if in_code(match.start()):
            continue
        index, depth = match.end(), 1
        while index < len(text) and depth:
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            index += 1
        if depth:
            continue
        alt = text[match.end():index - 1]
        if index < len(text) and text[index] == "(":
            parsed = _inline_destination(text, index + 1)
            if parsed is not None:
                start, end, source = parsed
                spans.append(Link(start, end, source))
                occurrences.append((match.start(), source))
            continue
        if index < len(text) and text[index] == "[":
            close = text.find("]", index + 1)
            label = text[index + 1:close] if close >= 0 else ""
        else:
            label = ""
        wanted = _label(label or alt)
        definition = definitions.get(wanted)
        if definition is None:
            continue
        used_labels.add(wanted)
        occurrences.append((match.start(), definition.source))

    spans.extend(definitions[label] for label in used_labels)
    unique = sorted({(link.start, link.end): link for link in spans}.values(),
                    key=lambda link: link.start)
    refs = tuple(source for _, source in sorted(occurrences, key=lambda item: item[0]))
    return Scan(links=tuple(unique), refs=refs)


def image_refs(text: str) -> tuple[str, ...]:
    """Image destinations in usage order (what a renderer would load, top to bottom)."""
    return scan(text).refs


def rewrite(text: str, mapping: dict[str, str]) -> str:
    """Replace destinations that match exactly, or match after normalization."""
    normalized = {canonical_ref(key): value for key, value in mapping.items()}
    for link in reversed(scan(text).links):
        replacement = mapping.get(link.source) or normalized.get(canonical_ref(link.source))
        if replacement is not None:
            text = text[:link.start] + replacement + text[link.end:]
    return text