"""Translate English Markdown to Chinese (OpenAI-compatible endpoint).

默认走本地配置好的 MiniMax-M3（凭据/端点由 config.py 解析：.secrets > 环境变量 > .env）。

Usage:
    python translate_md.py <input.md> [output.md]
                           [--provider minimax|deepseek|openrouter|ollama|lmstudio]
                           [--model MiniMax-M3] [--workers 8]
"""
import argparse
import os
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from openai import OpenAI


SYSTEM_PROMPT = """你是专业的学术论文翻译助手。将用户给出的英文 Markdown 翻译成中文，严格遵守以下规则：

1. 保留所有 Markdown 语法：标题（#、##）、列表、加粗、斜体、表格、代码块、引用块。
2. 行内公式 $...$ 和块级公式 $$...$$ 保持原样，不翻译公式内部任何字符。
3. 图片引用 ![](images/...) 与 HTML <img> 保持原样。
4. 代码块 ```lang ... ``` 保持原样，注释也不翻译。
5. 文献引用编号、URL、专有名词（人名、机构、模型名、数据集名）保持英文；首次出现时可在括号内附中文译名。
6. 表格内容翻译，表格分隔符（| --- |）保持原样；数字、单位、公式符号一律不译。
7. 图表标题（Figure 1、Table 2 ...）保留编号，只翻译说明文字。
8. 不得删减、合并或概括任何原文段落，段落顺序保持一致。
9. 标题：文字翻译成中文，但 `#` 的个数与层级必须与原文严格一致；
   原文中的普通段落（哪怕看起来像小标题）不要提升为标题，也不要凭空新增标题。
10. 输出只包含翻译后的 Markdown 正文，不要添加"以下是翻译"之类的说明、前言或后记。
"""


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_MATH_BLOCK_RE = re.compile(r"\$\$")
_THINK_RE = re.compile(r"<think>([\s\S]*?)(?:</think>|$)")


def clean_reasoning(text: str) -> str:
    """剥掉推理模型的思维链（MiniMax-M3 / DeepSeek-R1 等）。

    闭合的 <think>...</think> 和被 max_tokens 截断的未闭合 <think>... 都处理。
    """
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


def _is_split_safe(block: str) -> bool:
    """该段落能否独立成块：代码围栏内部、跨段落的 $$ 公式都不行。"""
    if _FENCE_RE.match(block):
        return False
    return len(_MATH_BLOCK_RE.findall(block)) % 2 == 0


def chunk_markdown(md: str, max_chars: int = 3000) -> list[str]:
    """按空行分块，每块不超过 max_chars；不在代码围栏/块级公式内部切开。"""
    blocks = md.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    fence: str | None = None      # 当前所处代码围栏标记（反引号或波浪线）
    math_open = False             # $$ 是否未闭合

    for block in blocks:
        # A cut is safe BEFORE this paragraph only if the preceding paragraph
        # ended outside a fence/formula. Scan every line so paired delimiters
        # in one paragraph cannot leave the state permanently open.
        splittable = fence is None and not math_open
        for line in block.splitlines():
            marker = _FENCE_RE.match(line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line.strip()[len(token):].strip():
                    fence = None
                continue
            if fence is None and len(_MATH_BLOCK_RE.findall(line)) % 2:
                math_open = not math_open

        p_len = len(block) + 2
        if current_len + p_len > max_chars and current and splittable:
            chunks.append("\n\n".join(current))
            current, current_len = [block], p_len
        else:
            current.append(block)
            current_len += p_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks



def translate_chunk(client: OpenAI, chunk: str, model: str = "deepseek-chat",
                    retries: int = 3) -> str:
    """Translate a single chunk with simple retry."""
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": chunk},
                ],
                temperature=0.3,
                stream=False,
            )
            content = resp.choices[0].message.content or ""
            return clean_reasoning(content)
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"\n  error: {e}; retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
    return ""  # unreachable


def translate_chunks_concurrent(
    client: OpenAI,
    chunks: list[str],
    model: str = "deepseek-chat",
    max_workers: int = 8,
    on_progress: Callable[[int, int, int], None] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_context: str = "",
) -> list[str]:
    """Translate chunks in parallel. Returns results in original chunk order.

    on_progress(completed, total, finished_idx) is called from the main thread
    after each chunk completes (in completion order, not chunk order).
    """
    n = len(chunks)
    results: list[str] = [""] * n
    if n == 0:
        return results

    # A checkpoint belongs to the exact source/model/config fingerprint supplied
    # by the caller. Persist each successful chunk atomically before progressing.
    completed_indices: set[int] = set()
    if checkpoint_path and checkpoint_path.exists():
        try:
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if (saved.get("model") == model and saved.get("context") == checkpoint_context
                    and saved.get("chunks") == chunks):
                for key, value in saved.get("results", {}).items():
                    idx = int(key)
                    if 0 <= idx < n and isinstance(value, str) and value:
                        results[idx] = value
                        completed_indices.add(idx)
        except (OSError, ValueError, TypeError):
            pass
    if checkpoint_path and completed_indices:
        print(f"  ↻ 续跑：已恢复 {len(completed_indices)}/{n} 个翻译块", flush=True)

    def persist() -> None:
        if not checkpoint_path:
            return
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model": model, "context": checkpoint_context, "chunks": chunks,
                   "results": {str(i): results[i] for i in sorted(completed_indices)}}
        temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, checkpoint_path)

    pending = [i for i in range(n) if i not in completed_indices]
    if on_progress:
        for done, idx in enumerate(sorted(completed_indices), 1):
            on_progress(done, n, idx)
    if max_workers <= 1:
        for idx in pending:
            results[idx] = translate_chunk(client, chunks[idx], model=model)
            completed_indices.add(idx)
            persist()
            if on_progress:
                on_progress(len(completed_indices), n, idx)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_to_idx = {ex.submit(translate_chunk, client, chunks[i], model): i for i in pending}
        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            results[idx] = fut.result()
            completed_indices.add(idx)
            persist()
            if on_progress:
                on_progress(len(completed_indices), n, idx)
    return results


def main():
    from config import ConfigError, resolve_translate

    parser = argparse.ArgumentParser(
        description="Translate English Markdown to Chinese (默认 MiniMax-M3)")
    parser.add_argument("input", type=Path, help="Input English Markdown file")
    parser.add_argument("output", type=Path, nargs="?",
                        help="Output Chinese Markdown file (default: <input>_zh.md)")
    parser.add_argument("--provider", default=None,
                        help="翻译后端预设（minimax 默认 / deepseek / openrouter / "
                             "ollama / lmstudio；不传则按 base_url 猜，再默认 minimax）")
    parser.add_argument("--model", default=None, help="模型名（默认取 .env / 预设）")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None,
                        help="显式 API key（不传则按 .secrets > 环境变量 > .env 解析）")
    parser.add_argument("--chunk-size", type=int, default=3000, help="Max chars per chunk")
    parser.add_argument("--workers", type=int, default=8,
                        help="Concurrent translation requests (default 8, use 1 for serial)")
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")

    try:
        cfg = resolve_translate(provider=args.provider, base_url=args.base_url,
                               model=args.model, api_key=args.api_key)
    except ConfigError as exc:
        sys.exit(str(exc))
    print(cfg.describe())

    output = args.output or args.input.with_name(args.input.stem + "_zh.md")

    md = args.input.read_text(encoding="utf-8")
    chunks = chunk_markdown(md, args.chunk_size)
    print(f"Split into {len(chunks)} chunks (max {args.chunk_size} chars each), "
          f"workers={args.workers}")

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=300,
                    max_retries=0)

    t0 = time.time()

    def on_progress(done, total, idx):
        print(f"  [{done}/{total}] chunk #{idx + 1} ok", flush=True)

    translated = translate_chunks_concurrent(
        client, chunks, model=cfg.model,
        max_workers=args.workers, on_progress=on_progress,
    )

    output.write_text("\n\n".join(clean_reasoning(t) for t in translated),
                      encoding="utf-8")
    print(f"\nDone: {output}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
