---
name: pdf-to-md
description: Converts a local PDF into a self-contained Markdown delivery directory through the MinerU cloud API, preserving heading structure, tables, formulas and extracted figures, with optional Chinese translation through an OpenAI-compatible model. Use when asked to 把 PDF 转成 Markdown、论文转 md、要保留图片和公式的 PDF 解析、把英文论文翻译成中文并保留插图, or whenever a PDF must become portable Markdown plus an images/ folder. Also matches 用 MinerU 解析这份 PDF and 批量把 PDF 变成 md. Do not use for docx/pptx/xlsx conversion, PDF layout reproduction, or merely reading, summarizing or answering questions about a paper.
compatibility: Pi coding agent; Python 3.10+; network access to mineru.net and one OpenAI-compatible endpoint
metadata:
  version: "0.1.0"
  status: "active"
  layer: "task"
  priority: "45"
  triggers: "pdf-to-markdown,pdf-to-md,mineru-ocr,pdf-figure-extraction,pdf-translation"
---

# PDF → Markdown

把一份 PDF 变成**可移植的 Markdown 交付目录**：文档结构保留、图片落地到 `images/`、翻译可选。

## 当前状态

| 项 | 状态 |
|---|---|
| 交付契约与通道判断 | ✅ 已定稿（`docs/PLAN.md`） |
| MinerU 官方文档整理 | ✅ `references/mineru-api.md` |
| `scripts/` 执行器 | ❌ 未实现（M1） |
| 可否安装到 skills 树 | ❌ **暂不可**，等 M1 门禁通过 |

本文件现在是设计与行为约定，不是可执行能力。不要对外声称它能跑。

## 何时使用

| 应当使用 | 不应使用 |
|---|---|
| 「把这份 PDF 转成 md，图片要留着」 | 「把 docx / pptx / xlsx 转 md」 |
| 「论文转 Markdown，公式和表格别丢」 | 「复刻 PDF 的排版/双栏版式」 |
| 「英文论文转中文，插图一起保留」 | 「解释/总结这篇论文」 |
| 「用 MinerU 解析这个 PDF」 | 「把 md 里的图片下载下来」 |

## 判断流程

1. **确认输入**：本地 PDF 绝对路径；文件存在且非空。
2. **是否需要图片**：需要 → `precision`；只想要纯文本 → `flash`。
3. **是否需要中文**：需要 → 翻译（模型 API）；只留英文 → 不翻译。
4. **确定输出位置**：默认与源 PDF 同级；用户指定则用指定目录。

### 通道的硬约束

| 通道 | Token | 大小/页数 | 图片 | 结构 JSON |
|---|---|---|---|---|
| `precision` | 需要 | ≤200 MB / ≤200 页 | ✅ 在 zip 内 | ✅ |
| `flash` | 不需要 | ≤10 MB / ≤20 页 | ❌ 仅 Markdown | ❌ |

**「保留图片」只有 `precision` 能满足。** `flash` 不是默认路径的降级替身：走 flash 时必须明确告诉用户「本次没有图片、没有结构 JSON」，不能把纯文本结果说成完整交付。

## 交付契约

```
<out>/<stem>/
├── <stem>.md             # MinerU 的 Markdown，图片链接已改写为 images/...
├── <stem>_zh.md          # 中文翻译；未翻译或翻译失败时不存在
├── <stem>.pdf            # 源 PDF 副本
├── images/               # 图片，扩展名与真实编码一致
└── _meta/
    ├── manifest.json     # 源文件 sha256、通道与参数、图片清单
    ├── validation.json   # 校验结果
    └── content_list.json # MinerU 结构 JSON（仅 precision）
```

三条不变量：

1. `images/` 之外不放图片；md 内只写 `images/<name>` 相对引用。
2. 交付目录内不出现缓存、检查点、临时文件。
3. 校验不通过就不是交付物——不要加「差不多可以用」的措辞。

## 执行（M1 落地后启用）

```bash
python3 scripts/doctor.py                      # 凭据自检
python3 scripts/pdf2md.py paper.pdf            # precision → 英文交付
python3 scripts/pdf2md.py paper.pdf --translate --target zh
python3 scripts/pdf2md.py paper.pdf --ocr flash   # 无 token，纯文本，无图
```

退出码：`0` 成功 / `1` 阶段失败 / `2` 用法或配置错误。

## 失败怎么报

| 情况 | 该说什么 |
|---|---|
| 没有 MinerU Token | 说明 precision 不可用、影响是没有图片；给出申请地址；询问是否改用 flash（并说明代价） |
| MinerU 返回失败 | 报 `err_msg` 与错误码原文，不要说「服务异常」了事 |
| 校验失败 | 列出具体 issue code（缺图/外链/扩展名不符/不可解码/中英图片序列不一致） |
| 轮询超时 | 说明是等待超时而非解析失败，给出可重试的建议 |
| 翻译失败 | 说明英文与检查点已保留、`_zh.md` 未生成 |

禁止：把半成品说成完成；把 flash 的纯文本说成「保留了图片」；在没有实际跑过时声称通过。

## 证据边界

- 只有实际跑过并看到 `_meta/validation.json` 的 `ok: true`，才能说交付有效。
- 图片是否可读，以实际解码结果为准，不以文件名后缀为准。
- 结构是否保留，以 md 里的标题层级/表格/公式计数为准，不做主观描述。

## 参考

- `references/mineru-api.md` — 两套 API 的端点、参数、状态机、错误码、语言表、待实测项
- `docs/PLAN.md` — 目标、范围、契约、关键决策、验收标准
- `docs/ROADMAP.md` — 阶段划分与门禁
