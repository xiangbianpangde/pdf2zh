#!/usr/bin/env python3
"""Opt-in trigger check for the pdf-to-md skill.

Two independent checks:

1. **Static** — the description must name the activation situations and must carry
   explicit "do not use" cues for the three near-neighbour tasks.
2. **Routing simulation** — the model that Pi would use is asked, with only the
   skill description in context, whether it would invoke the skill for a phrasing.
   This is a *simulation*, not Pi's real router: Pi builds the same decision from
   name + description, so a mismatch here is strong evidence of a description bug.

Cost: six short requests. Requires ``--confirm``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
import netclient  # noqa: E402
import translate  # noqa: E402

POSITIVE = (
    "把这篇论文转成 md，图片和公式都要留着",
    "PDF 转 Markdown 并翻译成中文",
    "用 MinerU 解析这份 PDF，保留插图",
)
NEGATIVE = (
    "把这个 docx 转成 md",
    "解释一下这篇论文讲了什么",
    "把 PDF 的排版原样复刻成网页",
)
NEGATIVE_CUES = ("do not use", "docx", "layout", "summariz")


def description_of(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^description:\s*(.+?)(?=^\w|^---)", text)
    if not match:
        raise SystemExit(f"description not found in {skill_md}")
    return " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())


def static_check(description: str) -> list[tuple[str, bool, str]]:
    lowered = description.lower()
    rows: list[tuple[str, bool, str]] = []
    rows.append(("包含 Markdown 转换的激活词", "markdown" in lowered or "md" in lowered, description[:60]))
    rows.append(("包含 PDF 输入范围", "pdf" in lowered, ""))
    rows.append(("包含「保留图片」语义", "figure" in lowered or "图片" in description, ""))
    for cue in NEGATIVE_CUES:
        rows.append((f"反例 cue：{cue}", cue in lowered, ""))
    rows.append(("未超出 1024 字符", len(description) <= 1024, f"{len(description)} 字符"))
    return rows


def ask(phrase: str, description: str, client: translate.Client) -> str:
    prompt = (
        "你是一个技能路由器。下面是唯一可用技能的名称与描述：\n\n"
        f"名称：pdf-to-md\n描述：{description}\n\n"
        f"用户请求：{phrase}\n\n"
        "这个技能是否应该被调用来完成该请求？只回答 YES 或 NO，不要解释。"
    )
    return client.complete(prompt).strip()


def verdict(reply: str) -> str:
    """Normalise YES/NO across languages; anything else is UNKNOWN."""
    head = reply.strip().upper()[:12]
    if "YES" in head or head.startswith(("是", "可以", "应")):
        return "YES"
    if "NO" in head or head.startswith(("否", "不", "无")):
        return "NO"
    return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(description="pdf-to-md 触发检查")
    parser.add_argument("--confirm", action="store_true", help="允许发起真实模型请求")
    parser.add_argument("--dry-run", action="store_true", help="只打印静态检查与用例")
    args = parser.parse_args()

    description = description_of(ROOT / "SKILL.md")
    print(f"description（{len(description)} 字符）")
    print("-" * 68)
    failures = 0
    for label, ok, detail in static_check(description):
        print(f"  {'✓' if ok else '✗'} {label}" + (f"：{detail}" if detail else ""))
        failures += 0 if ok else 1

    print()
    for phrase in POSITIVE + NEGATIVE:
        print(f"  · {phrase}")
    if args.dry_run:
        print("\n(dry-run：未发起模型请求)")
        return 0 if failures == 0 else 1
    if not args.confirm:
        print("\n需要真实模型请求：加 --confirm 运行（每次 6 个短请求）")
        return 0 if failures == 0 else 1

    settings = config.load_settings()
    try:
        base_url, model, api_key = settings.translation()
    except config.ConfigError as exc:
        print(f"\n✗ {exc}")
        return 2
    endpoint = translate.Endpoint(base_url=base_url.text, model=model.text,
                                  api_key=api_key.text, timeout=120)
    client = translate.Client(endpoint)
    print("-" * 68)
    print(f"routing simulation via {endpoint.describe()}")
    print(f"  {'期望':6s} {'实际':6s} 请求")
    for phrase, expected in zip(POSITIVE + NEGATIVE, ["YES"] * len(POSITIVE) + ["NO"] * len(NEGATIVE)):
        try:
            reply = ask(phrase, description, client)
        except Exception as exc:  # noqa: BLE001 - a probe reports everything
            print(f"  {expected:6s} {'ERR':6s} {phrase}  ← {type(exc).__name__}: {exc}")
            failures += 1
            continue
        actual = verdict(reply)
        ok = actual == expected
        print(f"  {expected:6s} {actual:6s} {phrase}{'' if ok else f'   ← MISMATCH（原始回复：{reply[:40]}）'}")
        failures += 0 if ok else 1

    print("-" * 68)
    print("结果：" + ("全部通过" if failures == 0 else f"{failures} 项需处理"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
