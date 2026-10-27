"""Convert OfficeCLI text output to Markdown.

Reads officecli text-mode output on stdin and writes Markdown to stdout.
Designed for xlsx / pptx / docx produced by `officecli view <file> text`.

xlsx lines look like:
    === Sheet: Sheet1 ===
    [/Sheet1/row[1]] A1=编号    B1=问题类型    C1=问题
    [/Sheet1/row[2]] A2=B1001    B2=数据基本查询    C2=...

pptx lines look like:
    === /slide[1] ===
    封面
    医学副脑 v3.0 收尾交付

docx lines look like:
    [/body/p[1]] 袁浩岚  Yuan Haolan
    [/body/tbl[1]] [Table: 5 rows]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CELL_RE = re.compile(r"^([A-Z]+)(\d+)=(.*)$")
TBL_SUMMARY_RE = re.compile(r"^\[Table:\s*(\d+)\s*rows?\]$")


def flush_table(rows: list[dict[str, str]], sheet: str, lines: list[str]) -> None:
    if not rows:
        return
    lines.append(f"### {sheet}")
    header = rows[0]
    header_keys = sorted(header.keys(), key=lambda key: (len(key), key))
    header_values = [_escape_cell(header.get(key, "")) for key in header_keys]
    lines.append("| " + " | ".join(header_keys) + " |")
    lines.append("| " + " | ".join("---" for _ in header_keys) + " |")
    lines.append("| " + " | ".join(header_values) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(_escape_cell(row.get(key, "")) for key in header_keys) + " |")
    lines.append("")


def _escape_cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "\\|").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert officecli text → Markdown")
    parser.add_argument("--source", choices=("auto", "xlsx", "pptx", "docx"), default="auto")
    parser.add_argument("--input", type=Path, default=None, help="Read from file instead of stdin")
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    lines_in = text.splitlines()
    out: list[str] = []

    sheet = ""
    current_rows: list[dict[str, str]] = []
    in_table = False
    pending_sheet = ""

    def end_table() -> None:
        nonlocal current_rows
        if current_rows:
            flush_table(current_rows, sheet or "Sheet", out)
        current_rows = []

    for line in lines_in:
        if not line.strip():
            continue

        sheet_match = re.match(r"^===\s*Sheet:\s*(.+?)\s*===\s*$", line)
        if sheet_match:
            end_table()
            sheet = sheet_match.group(1)
            pending_sheet = sheet
            in_table = False
            current_rows = []
            continue

        slide_match = re.match(r"^===\s*(/slide\[\d+\])\s*===\s*$", line)
        if slide_match:
            end_table()
            out.append(f"## {slide_match.group(1)}")
            in_table = False
            sheet = ""
            continue

        tbl_match = TBL_SUMMARY_RE.match(line.strip())
        if tbl_match and line.startswith("[/body/tbl"):
            out.append(f"<!-- {line.strip()} -->")
            continue

        if line.lstrip().startswith("[/") and "=" in line:
            path, _, cell_blob = line.partition(" ")
            rest = cell_blob.split("\t") if "\t" in cell_blob else re.split(r"\s{2,}", cell_blob)
            row: dict[str, str] = {}
            for part in rest:
                m = CELL_RE.match(part.strip())
                if not m:
                    continue
                key = m.group(1)
                row[key] = m.group(3)
            if row:
                if not in_table:
                    sheet = pending_sheet or sheet
                    in_table = True
                current_rows.append(row)
            continue

        if line.startswith("[/body/p["):
            end_table()
            payload = re.sub(r"^\[/body/p\[\d+\]\]\s*", "", line).strip()
            if payload:
                out.append(payload)
            in_table = False
            sheet = ""
            continue

        end_table()
        out.append(line)
        in_table = False
        sheet = ""

    end_table()
    sys.stdout.write("\n".join(out).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())