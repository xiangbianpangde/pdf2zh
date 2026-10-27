---
name: pdf-to-md
description: Converts a local PDF into a self-contained Markdown delivery directory through the MinerU cloud API, preserving heading structure, tables, formulas and extracted figures, with optional Chinese translation through any OpenAI-compatible model. Use when asked to 把 PDF 转成 Markdown、论文转 md、要保留图片和公式的 PDF 解析、把英文论文翻译成中文并保留插图、用 MinerU 解析这份 PDF, or whenever a PDF must become portable Markdown plus an images/ folder. Do not use for docx/pptx/xlsx conversion, PDF layout reproduction, or merely reading, summarizing or answering questions about a paper.
compatibility: Pi coding agent; Python 3.10+; network access to mineru.net and one OpenAI-compatible endpoint
metadata:
  version: "0.2.1"
  status: "active"
  layer: "task"
  priority: "45"
  triggers: "pdf-to-markdown,pdf-to-md,mineru-ocr,pdf-figure-extraction,pdf-translation"
---

# PDF → Markdown

把一份 PDF 变成**可移植的 Markdown 交付目录**：文档结构保留、图片落地到 `images/`、翻译可选。

## 状态

| 能力 | 状态 |
|---|---|
| precision 通道（含图片、结构 JSON） | ✅ 真实 PDF 实测通过 |
| flash 通道（免 Token，纯 Markdown） | ✅ 真实 PDF 实测通过 |
| 图片归一化 + 链接改写 + 硬校验 | ✅ 实测 67 张图、0 issue |
| 可选翻译（任意 OpenAI 兼容端点） | ✅ 真实模型实测，中英图片序列一致 |
| 批量、并发、OCR 缓存 | ✅ 压测通过：3 份并发 47 s，复用 0 次外部调用 |
| 离线测试 | ✅ `python3 -m unittest discover -s tests -t tests` 45 项 |

## 什么时候用

| 用 | 不用 |
|---|---|
| 「把这份 PDF 转成 md，图片要留着」 | 「把 docx / pptx / xlsx 转 md」 |
| 「论文转 Markdown，公式和表格别丢」 | 「复刻 PDF 的排版/双栏版式」 |
| 「英文论文转中文，插图一起保留」 | 「解释/总结这篇论文」 |
| 「用 MinerU 解析这个 PDF」 | 「把 md 里的图片下载下来」 |

## 怎么用

```bash
# 1) 自检（会打印每个凭据来自哪里，密钥只显示前后几位）
python3 scripts/pdf2md.py --doctor

# 2) 只转英文（默认 precision，需 MinerU Token）
python3 scripts/pdf2md.py /abs/paper.pdf -o /abs/out

# 3) 同时翻译：模型参数全部显式传入，随时可换
python3 scripts/pdf2md.py /abs/paper.pdf -o /abs/out --translate \
  --base-url https://api.example.com/v1 --model some-model --api-key sk-...

# 4) 没有 Token：纯文本，没有图片
python3 scripts/pdf2md.py /abs/paper.pdf --ocr flash
```

关键参数：`--ocr precision|flash`、`--translate`、`--base-url`、`--model`、`--api-key`、`--chunk-size`、`--workers`、`--pages`、`--language`、`--force`、`--json`、`--parallelism`。

凭据也可以放到 `./.secrets/pdf2md.json`、`~/.config/pdf2md/secrets.json`、环境变量或 `./.env`；优先级见 `references/translation.md`。

## 判断流程

1. **要图片吗？** 要 → `precision`（需 Token）。只想要文字 → `flash`，并明确告知用户本次没有图片。
2. **要中文吗？** 要 → `--translate`。先确认 `--base-url/--model/--api-key` 或已存凭据齐全。
3. **输出放哪？** 默认与源 PDF 同级；用户指定则用 `-o`。
4. **跑完看什么？** `_meta/validation.json` 的 `ok`、`image_count`，以及终端最后一行目录路径。

### 通道硬约束

| 通道 | Token | 大小/页数 | 图片 | 结构 JSON |
|---|---|---|---|---|
| `precision` | 需要 | ≤200 MB / ≤200 页 | ✅ | ✅ |
| `flash` | 不需要 | ≤10 MB / ≤20 页（超页用 `--pages 1-20`） | ❌ | ❌ |

**「保留图片」只有 `precision` 能满足。** 实测 flash 会输出结构完整但**零图片引用**的 Markdown，所以它不是降级替身，是另一种交付。flash 超过 20 页会报错并提示用 `--pages`（实测 44 页文件传 `--pages 1-20` 可成功）。

## 交付契约（摘要）

```
<out>/<stem>/
├── <stem>.md            图片链接已改写为 images/...
├── <stem>_zh.md         中文（未翻译/翻译失败时不存在）
├── <stem>.pdf           源 PDF 副本
├── images/              扩展名与真实编码一致
└── _meta/               manifest / validation / completion / content_list
```

三条不变量：图片只在 `images/`；交付目录内没有缓存与临时文件；校验不通过就不是交付物。

细节（命名规则、九类校验码、缓存与重跑语义）见 `references/output-contract.md`。

## 失败怎么报

| 情况 | 该说什么 |
|---|---|
| 没有 MinerU Token | precision 不可用、影响是没有图片；给出申请地址；询问是否改用 flash 并说明代价 |
| MinerU 报错 | 引用 `err_msg` 原文与错误码；`A0202/A0211` 指向 Token 过期 |
| 校验失败 | 列出具体 code（缺图/外链/路径不可移植/扩展名不符/不可解码/中英序列不一致） |
| 等待超时 | 说明是等待超时而非解析失败，建议加大 `--timeout` 或稍后重试 |
| 翻译失败 | 英文与检查点已保留、`_zh.md` 未写；如需重试只需重跑同一命令 |
| 端点 403 + `1010` | 是网关拦 UA，不是 key 问题；说明已固定 UA，让用户回报网关 |

禁止：把半成品说成完成；把 flash 的纯文本说成「保留了图片」；没跑过就说通过。

## 证据边界

- 只有 `_meta/validation.json` 里 `ok: true` 才可以说交付有效。
- 图片是否可用，以实际解码结果为准，不以文件名后缀为准。
- 结构是否保留，以标题/表格/公式计数与中英图片序列比对为准，不用主观描述。
- 报告时区分「离线测试通过」与「真实 PDF 跑通过」——两者不能互相替代。

## 参考

- `references/mineru-api.md` — 两套 API 的端点、参数、状态机、错误码、语言表、待实测项
- `references/output-contract.md` — 目录、命名、图片规则、九类校验码、缓存与重跑
- `references/translation.md` — 三个模型参数、端点拼接、网关兼容、分块、检查点、失败语义
- `docs/PLAN.md` / `docs/ROADMAP.md`（仓库根）— 目标、范围、阶段门禁
