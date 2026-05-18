"""Translate English Markdown to Chinese using DeepSeek.

Usage:
    set DEEPSEEK_API_KEY=sk-xxxxxxxx
    python translate_md.py <input.md> [output.md] [--model deepseek-chat]
                                                  [--workers 8]
"""
import argparse
import os
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
6. 表格内容翻译，表格分隔符（| --- |）保持原样。
7. 输出只包含翻译后的 Markdown 正文，不要添加"以下是翻译"之类的说明、前言或后记。
"""


def chunk_markdown(md: str, max_chars: int = 3000) -> list[str]:
    """按段落分块，每块不超过 max_chars。优先在 \\n\\n 处切。"""
    paragraphs = md.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for p in paragraphs:
        p_len = len(p) + 2
        if current_len + p_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [p]
            current_len = p_len
        else:
            current.append(p)
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
            return resp.choices[0].message.content or ""
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
) -> list[str]:
    """Translate chunks in parallel. Returns results in original chunk order.

    on_progress(completed, total, finished_idx) is called from the main thread
    after each chunk completes (in completion order, not chunk order).
    """
    n = len(chunks)
    results: list[str] = [""] * n
    if n == 0:
        return results
    if max_workers <= 1:
        for i, chunk in enumerate(chunks):
            results[i] = translate_chunk(client, chunk, model=model)
            if on_progress:
                on_progress(i + 1, n, i)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_to_idx = {
            ex.submit(translate_chunk, client, chunk, model): i
            for i, chunk in enumerate(chunks)
        }
        completed = 0
        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            results[idx] = fut.result()
            completed += 1
            if on_progress:
                on_progress(completed, n, idx)
    return results


def main():
    parser = argparse.ArgumentParser(description="Translate English Markdown to Chinese via DeepSeek")
    parser.add_argument("input", type=Path, help="Input English Markdown file")
    parser.add_argument("output", type=Path, nargs="?",
                        help="Output Chinese Markdown file (default: <input>_zh.md)")
    parser.add_argument("--model", default="deepseek-chat",
                        help="DeepSeek model (deepseek-chat or deepseek-reasoner)")
    parser.add_argument("--chunk-size", type=int, default=3000, help="Max chars per chunk")
    parser.add_argument("--workers", type=int, default=8,
                        help="Concurrent translation requests (default 8, use 1 for serial)")
    parser.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY"),
                        help="DeepSeek API key (or set DEEPSEEK_API_KEY)")
    parser.add_argument("--base-url", default="https://api.deepseek.com",
                        help="API base URL")
    args = parser.parse_args()

    if not args.api_key:
        sys.exit("Missing API key. Set DEEPSEEK_API_KEY env var or pass --api-key")
    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")

    output = args.output or args.input.with_name(args.input.stem + "_zh.md")

    md = args.input.read_text(encoding="utf-8")
    chunks = chunk_markdown(md, args.chunk_size)
    print(f"Split into {len(chunks)} chunks (max {args.chunk_size} chars each), "
          f"workers={args.workers}")

    client = OpenAI(api_key=args.api_key, base_url=args.base_url)

    t0 = time.time()

    def on_progress(done, total, idx):
        print(f"  [{done}/{total}] chunk #{idx + 1} ok", flush=True)

    translated = translate_chunks_concurrent(
        client, chunks, model=args.model,
        max_workers=args.workers, on_progress=on_progress,
    )

    output.write_text("\n\n".join(translated), encoding="utf-8")
    print(f"\nDone: {output}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
