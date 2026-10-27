"""Optional translation through an OpenAI-compatible ``/chat/completions`` endpoint.

Everything here is provider-agnostic: base URL, model name and key are runtime
parameters, so switching models never touches code (see ``references/translation.md``).

Two transport details are real-world requirements, not preferences:

* an explicit ``User-Agent`` (some front ends reject the default urllib one with a
  Cloudflare ``1010`` page instead of a JSON error);
* ``x-opencode-session`` for opencode.ai endpoints, which refuses to route without
  a session id. Other providers never see it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import netclient
from errors import TranslationError
from organize import atomic_json

__all__ = [
    "Client",
    "Endpoint",
    "clean_reasoning",
    "split_markdown",
    "translate_text",
]

SYSTEM_PROMPT = """你是专业的学术文档翻译助手。把用户给出的英文 Markdown 翻译成中文，严格遵守以下规则：

1. 保留全部 Markdown 语法：标题、列表、加粗、斜体、表格、代码块、引用块。
2. 行内公式 $...$ 与块级公式 $$...$$ 原样保留，公式内部任何字符都不翻译。
3. 图片引用 ![alt](images/...) 与 HTML <img> 原样保留，不新增、不删除、不改顺序。
4. 代码块 ```lang ... ``` 原样保留，注释也不翻译。
5. 文献引用编号、URL、专有名词（人名、机构、模型名、数据集名）保留英文；首次出现可在括号内附中文译名。
6. 表格内容翻译，分隔行（| --- |）与列数保持原样；数字、单位、公式符号不译。
7. 图表标题（Figure 1、Table 2 …）保留编号，只翻译说明文字。
8. 不得删减、合并或概括任何原文段落，段落顺序与数量保持一致。
9. 标题：文字译为中文，但 # 的个数与层级必须与原文严格一致；普通段落不得提升为标题，也不得凭空新增标题。
10. 只输出翻译后的 Markdown 正文，不要添加「以下是翻译」之类的说明、前言或后记。
"""

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_MATH_BLOCK = re.compile(r"\$\$")
_THINK = re.compile(r"<think\b[^>]*>([\s\S]*?)(?:</think>|$)", re.IGNORECASE)


def clean_reasoning(text: str) -> str:
    """Drop a reasoning model's ``<think>`` block, including an unterminated one."""
    if not text:
        return ""
    return _THINK.sub("", text).strip()


def _split_safe(block: str) -> bool:
    if _FENCE.match(block):
        return False
    return len(_MATH_BLOCK.findall(block)) % 2 == 0


def split_markdown(md: str, max_chars: int = 3000) -> list[str]:
    """Split on blank lines without ever cutting inside a fence or a ``$$`` block."""
    if max_chars < 1:
        raise ValueError("max_chars 必须为正数")
    blocks = md.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    fence: str | None = None
    math_open = False

    for block in blocks:
        splittable = fence is None and not math_open
        for line in block.splitlines():
            marker = _FENCE.match(line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line.strip()[len(token):].strip():
                    fence = None
                continue
            if fence is None and len(_MATH_BLOCK.findall(line)) % 2:
                math_open = not math_open
        length = len(block) + 2
        if current_length + length > max_chars and current and splittable:
            chunks.append("\n\n".join(current))
            current, current_length = [block], length
        else:
            current.append(block)
            current_length += length
    if current:
        chunks.append("\n\n".join(current))
    return chunks


@dataclass(frozen=True)
class Endpoint:
    """Where and how to reach one OpenAI-compatible model."""

    base_url: str
    model: str
    api_key: str
    timeout: float = 300.0
    retries: int = 3
    extra_headers: dict[str, str] = field(default_factory=dict)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def url(self) -> str:
        base = self.base_url.strip().rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if "opencode.ai" in urlsplit(self.base_url).netloc:
            headers["x-opencode-session"] = self.session_id
        headers.update(self.extra_headers)
        return headers

    def describe(self) -> str:
        return f"{self.model} @ {self.base_url}"


class Client:
    """One model endpoint. Thread-safe: requests share no mutable state."""

    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint

    def complete(self, prompt: str) -> str:
        endpoint = self.endpoint
        body = {
            "model": endpoint.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "stream": False,
        }
        try:
            response = netclient.request(endpoint.url, method="POST", json_body=body,
                                         headers=endpoint.headers, timeout=endpoint.timeout,
                                         retries=endpoint.retries)
        except ConnectionError as exc:
            raise TranslationError(f"连接翻译端点失败：{exc}") from exc
        if response.status != 200:
            raise TranslationError(
                f"翻译请求失败：HTTP {response.status}，{response.text[:300]}",
                status=response.status)
        try:
            payload = response.json()
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
        except ValueError as exc:
            raise TranslationError(f"翻译响应不是合法 JSON：{exc}") from exc
        content = clean_reasoning(str(message.get("content") or ""))
        if not content:
            reasoning = str(message.get("reasoning_content") or "").strip()
            hint = "（只返回了 reasoning_content）" if reasoning else ""
            raise TranslationError(f"翻译返回空内容{hint}")
        return content


class Checkpoint:
    """Per-chunk results that survive an interruption, keyed by exact context."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self, *, model: str, base_url: str, context: str,
             chunks: list[str]) -> dict[int, str]:
        if not self.path.is_file():
            return {}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {}
        if (saved.get("model") != model or saved.get("base_url") != base_url
                or saved.get("context") != context or saved.get("chunks") != chunks):
            return {}
        results: dict[int, str] = {}
        for key, value in (saved.get("results") or {}).items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(chunks) and isinstance(value, str) and value.strip():
                results[index] = value
        return results

    def save(self, *, model: str, base_url: str, context: str,
             chunks: list[str], results: dict[int, str]) -> None:
        payload = {
            "model": model,
            "base_url": base_url,
            "context": context,
            "chunks": chunks,
            "results": {str(index): results[index] for index in sorted(results)},
        }
        with self._lock:
            atomic_json(self.path, payload)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def translate_text(text: str, client: Client, *, chunk_size: int = 3000,
                   workers: int = 8, checkpoint: Checkpoint | None = None,
                   context: str = "",
                   on_progress: Callable[[int, int, int], None] | None = None,
                   report: Callable[[str], None] | None = None) -> str:
    """Translate a whole Markdown document, resuming completed chunks when possible."""
    chunks = split_markdown(text, chunk_size)
    if not chunks:
        return ""
    endpoint = client.endpoint
    results: dict[int, str] = {}
    if checkpoint is not None:
        results = checkpoint.load(model=endpoint.model, base_url=endpoint.base_url,
                                  context=context, chunks=chunks)
        if results and report:
            report(f"  续跑：已恢复 {len(results)}/{len(chunks)} 个翻译块")

    pending = [index for index in range(len(chunks)) if index not in results]
    if workers <= 1 or len(pending) <= 1:
        for index in pending:
            results[index] = client.complete(chunks[index])
            if checkpoint is not None:
                checkpoint.save(model=endpoint.model, base_url=endpoint.base_url,
                                context=context, chunks=chunks, results=results)
            if on_progress:
                on_progress(len(results), len(chunks), index)
        return "\n\n".join(results[index] for index in range(len(chunks)))

    with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
        futures = {pool.submit(client.complete, chunks[index]): index for index in pending}
        try:
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
                if checkpoint is not None:
                    checkpoint.save(model=endpoint.model, base_url=endpoint.base_url,
                                    context=context, chunks=chunks, results=results)
                if on_progress:
                    on_progress(len(results), len(chunks), index)
        except Exception:
            for future in futures:
                future.cancel()
            raise
    missing = [index for index in range(len(chunks)) if not str(results.get(index, "")).strip()]
    if missing:
        raise TranslationError(f"有 {len(missing)} 个翻译块返回空内容，未生成中文文件")
    return "\n\n".join(results[index] for index in range(len(chunks)))


def parse_header(value: str) -> tuple[str, str]:
    """``"Name: value"`` → ``("Name", "value")`` for ``--api-header``."""
    if ":" not in value:
        raise ValueError(f"--api-header 需要 NAME: VALUE 形式，收到 {value!r}")
    name, raw = value.split(":", 1)
    name = name.strip()
    if not name:
        raise ValueError("--api-header 缺少名称")
    return name, raw.strip()
