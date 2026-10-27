"""Safe HTML reading previews (reflowed, not a reproduction of PDF layout)."""
from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt


class _SafeHTML(HTMLParser):
    tags = set("p br hr h1 h2 h3 h4 h5 h6 ul ol li strong em b i del s blockquote pre code table thead tbody tfoot tr th td caption img a sup sub span div details summary".split())
    void = {"br", "hr", "img"}
    attrs = {"href", "src", "alt", "title", "colspan", "rowspan", "start", "class"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.blocked += 1
            return
        if self.blocked or tag not in self.tags:
            return
        safe = []
        for key, value in attrs:
            if key not in self.attrs or value is None:
                continue
            if key in {"src", "href"}:
                cleaned = "".join(char for char in value if ord(char) > 32)
                scheme = urlsplit(cleaned).scheme.lower()
                if scheme not in {"", "https", "http", "mailto"} and not (key == "src" and value.startswith(("data:image/png;", "data:image/jpeg;"))):
                    continue
            safe.append(f' {key}="{html.escape(value, quote=True)}"')
        self.output.append(f"<{tag}{''.join(safe)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"} and self.blocked:
            self.blocked -= 1
        elif not self.blocked and tag in self.tags and tag not in self.void:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked:
            self.output.append(html.escape(data))


def _math_inline(state, silent):
    start = state.pos
    if state.src[start] != "$":
        return False
    marker = "$$" if state.src.startswith("$$", start) else "$"
    end = state.src.find(marker, start + len(marker))
    if end < 0 or (marker == "$" and "\n" in state.src[start:end]):
        return False
    if not silent:
        token = state.push("math_inline", "span", 0)
        token.content = state.src[start:end + len(marker)]
    state.pos = end + len(marker)
    return True


def _math_block(state, start_line, end_line, silent):
    start = state.bMarks[start_line] + state.tShift[start_line]
    line = state.src[start:state.eMarks[start_line]].strip()
    if not line.startswith("$$"):
        return False
    if line.endswith("$$") and len(line) > 2:
        end = start_line + 1
    elif line == "$$":
        end = start_line + 1
        while end < end_line and state.src[state.bMarks[end]:state.eMarks[end]].strip() != "$$":
            end += 1
        if end == end_line:
            return False
        end += 1
    else:
        return False
    if silent:
        return True
    token = state.push("math_block", "div", 0)
    token.content = state.getLines(start_line, end, state.blkIndent, False).strip()
    token.map = [start_line, end]
    state.line = end
    return True


def _render_math(tokens, index, options, env):
    tag = "div" if tokens[index].type == "math_block" else "span"
    return f'<{tag} class="math">{html.escape(tokens[index].content)}</{tag}>'


_HTML_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ max-width: 980px; margin: 0 auto; padding: 2rem 1.25rem 4rem; color: #202124; background: #fff; line-height: 1.7; }}
img {{ max-width: 100%; height: auto; display: block; margin: 1rem auto; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; overflow: auto; display: block; }}
th, td {{ border: 1px solid #d8dce2; padding: .45rem .65rem; text-align: left; }}
th {{ background: #f4f6f8; }}
pre {{ overflow-x: auto; padding: 1rem; background: #f6f8fa; border-radius: .4rem; }}
code {{ background: #f6f8fa; padding: .1rem .25rem; border-radius: .2rem; }}
blockquote {{ border-left: 4px solid #b8c7d9; margin-left: 0; padding-left: 1rem; color: #4b5563; }}
.math {{ overflow-x: auto; white-space: pre-wrap; }}
.preview-note {{ color: #526170; font-size: .85rem; border-bottom: 1px solid #e1e5e9; padding-bottom: 1rem; }}
</style>
</head>
<body>
<p class="preview-note">Reading preview / 阅读预览：采用流式排版，不复刻 PDF 页式布局。公式联网加载 MathJax；离线保留 LaTeX。</p>
{body}
</body>
</html>
"""


def render_markdown_file(markdown_path: Path, html_path: Path | None = None, *, lang: str = "zh-CN") -> Path:
    markdown_path = Path(markdown_path)
    html_path = Path(html_path or markdown_path.with_suffix(".html"))
    text = markdown_path.read_text(encoding="utf-8")
    renderer = MarkdownIt("default", {"html": True, "linkify": False, "typographer": False})
    renderer.inline.ruler.before("escape", "math_inline", _math_inline)
    renderer.block.ruler.before("fence", "math_block", _math_block)
    renderer.renderer.rules["math_inline"] = _render_math
    renderer.renderer.rules["math_block"] = _render_math
    sanitizer = _SafeHTML()
    sanitizer.feed(renderer.render(text))
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_HTML_TEMPLATE.format(lang=html.escape(lang),
                         title=html.escape(markdown_path.stem), body="".join(sanitizer.output)), encoding="utf-8")
    return html_path
